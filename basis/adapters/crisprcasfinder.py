"""Pinned CRISPRCasFinder 4.3.2 / MacSyFinder 2.0 adapter.

Native source: dcouvin/CRISPRCasFinder@c13475ebf983040b3fb0b685380e7da317752002.
CCF swallows Cas subprocess errors and drops unordered-mode Cas results; we verify
MacSyFinder's completion artifacts and retain unordered component candidates.
"""
from __future__ import annotations

import configparser
import csv
import hashlib
import io
import json
import math
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from ..inputs import read_fasta

VERSION = "4.3.2"
SOURCE_COMMIT = "c13475ebf983040b3fb0b685380e7da317752002"
SOURCE_SHA256 = "025ace168429a7004d9b9860a8e8facabe44f6873b043d4ac537fe86ccb732fc"
MACSYFINDER_VERSION = "2.0"
CAS_MODELS_VERSION = "3.1.0"
_BASES = re.compile(r"[ACGTRYSWKMBDHVN]+", re.I)
_ERROR = re.compile(r"An error occurred|\bTraceback\b|^.*\b(?:ERROR|CRITICAL)\b|not installed|failure:", re.M)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_MACSY_FIELDS = {"replicon", "hit_id", "gene_name", "model_fqn", "sys_id", "hit_status", "hit_i_eval",
                 "hit_score", "hit_profile_cov", "hit_seq_cov", "hit_seq_len", "hit_begin_match", "hit_end_match"}

# MacSyFinder 2.0 reads ambient configurations BEFORE --cfg-file; that flag
# alone is not isolation. These saved run-local shims disable only those two
# readers in the native Python process, preserving defaults, model config,
# the explicit project config and CCF's CLI settings. Upstream files stay intact.
_MACSY_SHIM = r'''#!/usr/bin/env python
import hashlib, json, os, runpy, sys
from pathlib import Path
import macsypy
from macsypy.config import Config

root = Path(__file__).resolve().parent.parent
manifest = json.loads((root / "runtime_isolation.json").read_text())
actual = Path(manifest["macsyfinder_executable"])
cfg = root / "macsyfinder.conf"
record = {"policy": "disable_system_and_user_config_readers", "macsyfinder_version": macsypy.__version__,
          "python": sys.executable, "actual_executable": str(actual), "ignored_configurations": [],
          "explicit_config": str(cfg), "config_sha256": hashlib.sha256(cfg.read_bytes()).hexdigest(),
          "wrapper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "exit_status": 1}
audit = root / "macsyfinder_isolation.json"
try:
    if macsypy.__version__ != "2.0":
        raise RuntimeError("The isolated CRISPRCasFinder adapter requires MacSyFinder 2.0")
    if actual.resolve() == Path(__file__).resolve() or not actual.is_file():
        raise RuntimeError("Invalid or recursive MacSyFinder executable")
    if hashlib.sha256(actual.read_bytes()).hexdigest() != manifest["macsyfinder_executable_sha256"]:
        raise RuntimeError("MacSyFinder executable changed after isolation setup")
    if any(a == "--cfg-file" or a.startswith("--cfg-file=") or a == "--previous-run" for a in sys.argv[1:]):
        raise RuntimeError("Unexpected native configuration override")
    def ignored(scope):
        def skip(self, path):
            record["ignored_configurations"].append({"scope": scope, "path": str(path)})
        return skip
    for method, scope in (("_set_system_wide_config", "system"), ("_set_user_wide_config", "user")):
        if not callable(getattr(Config, method, None)):
            raise RuntimeError("Unsupported MacSyFinder configuration API")
        setattr(Config, method, ignored(scope))
    sys.argv = [str(actual), *sys.argv[1:], "--cfg-file", str(cfg)]
    record["argv"] = sys.argv
    runpy.run_path(str(actual), run_name="__main__")
    record["exit_status"] = 0
except SystemExit as exc:
    record["exit_status"] = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    raise
finally:
    audit.write_text(json.dumps(record, indent=2) + "\n")
'''

_CCF_LAUNCHER = r'''import hashlib, json, os, shutil, sys
from pathlib import Path
root = Path(__file__).resolve().parent
actual = shutil.which("macsyfinder")
if actual is None:
    raise RuntimeError("MacSyFinder is unavailable in the selected native environment")
actual = Path(actual).resolve()
shim = root / "bin" / "macsyfinder"
if actual == shim.resolve():
    raise RuntimeError("Recursive MacSyFinder wrapper")
record = {"policy": "disable_system_and_user_config_readers", "python": sys.executable,
          "macsyfinder_executable": str(actual), "macsyfinder_executable_sha256": hashlib.sha256(actual.read_bytes()).hexdigest(),
          "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          "wrapper_sha256": hashlib.sha256(shim.read_bytes()).hexdigest(),
          "config_sha256": hashlib.sha256((root / "macsyfinder.conf").read_bytes()).hexdigest(),
          "home_environment_unchanged": True}
(root / "runtime_isolation.json").write_text(json.dumps(record, indent=2) + "\n")
env = os.environ.copy()
env["PATH"] = str(shim.parent) + os.pathsep + env.get("PATH", "")
os.execvpe(sys.argv[1], sys.argv[1:], env)
'''


def _verify_runtime_isolation(work, cas_status):
    manifest = json.loads(_read(work / "runtime_isolation.json"))
    expected = {"launcher_sha256": hashlib.sha256(_CCF_LAUNCHER.encode()).hexdigest(),
                "wrapper_sha256": hashlib.sha256(_MACSY_SHIM.encode()).hexdigest(),
                "config_sha256": hashlib.sha256((work / "macsyfinder.conf").read_bytes()).hexdigest()}
    if any(manifest.get(key) != value for key, value in expected.items()) or manifest.get("policy") != "disable_system_and_user_config_readers" or manifest.get("home_environment_unchanged") is not True:
        raise ValueError("CRISPRCasFinder runtime configuration isolation was not verified")
    if cas_status["status"] == "completed":
        audit = json.loads(_read(work / "macsyfinder_isolation.json"))
        if (audit.get("exit_status") != 0 or audit.get("macsyfinder_version") != MACSYFINDER_VERSION
                or audit.get("policy") != manifest["policy"] or audit.get("actual_executable") != manifest.get("macsyfinder_executable")
                or audit.get("wrapper_sha256") != expected["wrapper_sha256"] or audit.get("config_sha256") != expected["config_sha256"]
                or audit.get("python") != manifest.get("python")):
            raise ValueError("MacSyFinder did not complete with verified native-environment configuration isolation")


def _read(path, empty=False):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Required CRISPRCasFinder output missing: {path}")
    text = path.read_text(encoding="utf-8-sig")
    if not empty and not text.strip():
        raise ValueError(f"Required CRISPRCasFinder output empty: {path}")
    if text.lstrip().lower().startswith(("<!doctype", "<html")):
        raise ValueError(f"HTML is not a native output: {path}")
    return text


def _int(value, name, minimum=0):
    if isinstance(value, bool) or not re.fullmatch(r"-?\d+", str(value)) or int(value) < minimum:
        raise ValueError(f"Invalid {name}: {value!r}")
    return int(value)


def _number(value, name, minimum=0, maximum=None):
    if isinstance(value, bool):
        raise ValueError(f"Invalid {name}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {name}") from exc
    if not math.isfinite(number) or number < minimum or (maximum is not None and number > maximum):
        raise ValueError(f"Invalid {name}: {value!r}")
    return number


def _interval(row, length):
    left, right = _int(row.get("Start"), "start", 1), _int(row.get("End"), "end", 1)
    if not left <= right <= length:
        raise ValueError("CRISPRCasFinder coordinates exceed or contradict input sequence")
    return left, right


def _name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing {label}")
    return value


def _strand(value):
    if not isinstance(value, str):
        raise ValueError("CRISPRCasFinder orientation must be text")
    if value in {"ND", "."}:
        return None
    if value not in {"+", "-"}:
        raise ValueError(f"Invalid CRISPRCasFinder orientation: {value!r}")
    return value


def _finding(contig, gene, family, start, end, feature, native, **attrs):
    return {"tool": "crisprcasfinder", "category": "crispr", "gene": gene,
            "family": family, "contig": contig, "start": start, "end": end,
            "protein_id": None, "strand": None, "reference": None,
            "identity": None, "coverage": None, "evidence_level": "prediction",
            "interpretation": "predicted_context",
            "attributes": {"feature_type": feature, "native": native,
                           "coordinate_basis": "1-based inclusive", **attrs}}


def _parse_arrays(arrays, contig, sequence, min_level):
    if not isinstance(arrays, list):
        raise ValueError("CRISPRCasFinder requires a Crisprs list for every sequence")
    findings, seen = [], set()
    for array in arrays:
        if not isinstance(array, dict):
            raise ValueError("Malformed CRISPR array")
        ident = _name(array.get("Name"), "CRISPR array name")
        if ident in seen:
            raise ValueError("Duplicate CRISPR array name")
        seen.add(ident)
        start, end = _interval(array, len(sequence))
        repeat = _name(array.get("DR_Consensus"), "repeat consensus").upper()
        repeat_length = _int(array.get("DR_Length"), "repeat length", 1)
        if not _BASES.fullmatch(repeat) or len(repeat) != repeat_length:
            raise ValueError("Invalid CRISPR repeat consensus/length")
        spacers = _int(array.get("Spacers"), "spacer count", 1)
        level = _int(array.get("Evidence_Level"), "evidence level", 1)
        if level > 4:
            raise ValueError("Unsupported CRISPRCasFinder evidence level")
        orientation = _strand(array.get("Potential_Orientation"))
        _strand(array.get("CRISPRDirection"))
        _number(array.get("Conservation_DRs"), "repeat conservation", maximum=100)
        _number(array.get("Conservation_Spacers"), "spacer conservation", maximum=100)
        regions = array.get("Regions")
        if not isinstance(regions, list):
            raise ValueError("CRISPR array lacks native Regions")
        parts, flanks = [], set()
        for region in regions:
            if not isinstance(region, dict):
                raise ValueError("Malformed CRISPR region")
            kind = region.get("Type")
            if kind not in {"DR", "Spacer", "LeftFLANK", "RightFLANK"}:
                raise ValueError("Unsupported CRISPR region type")
            bases = _name(region.get("Sequence"), "region sequence").upper()
            if kind in {"LeftFLANK", "RightFLANK"}:
                if kind in flanks:
                    raise ValueError("Duplicate CRISPR flank")
                flanks.add(kind)
                # Upstream writes out-of-bounds nominal flanks with UNKNOWN at contig ends.
                left = _int(region.get("Start"), "flank start", -1000000)
                right = _int(region.get("End"), "flank end", 0)
                if right < left or (kind == "LeftFLANK" and right != start - 1) or (kind == "RightFLANK" and left != end + 1):
                    raise ValueError("CRISPR flank coordinates contradict array")
                if bases == "UNKNOWN":
                    if 1 <= left <= right < len(sequence):
                        raise ValueError("Unexpected UNKNOWN CRISPR flank away from boundary")
                elif not _BASES.fullmatch(bases) or not 1 <= left <= right <= len(sequence) or bases != sequence[left - 1:right]:
                    raise ValueError("CRISPR flank sequence does not match assembly")
                _int(region.get("Leader"), "leader flag")
                _number(region.get("AT"), "flank AT", maximum=100)
                continue
            left, right = _interval(region, len(sequence))
            if not start <= left <= right <= end or not _BASES.fullmatch(bases) or bases != sequence[left - 1:right]:
                raise ValueError("CRISPR repeat/spacer sequence does not match assembly")
            parts.append((kind, left, right))
        expected_kinds = [value for _ in range(spacers) for value in ("DR", "Spacer")] + ["DR"]
        if [p[0] for p in parts] != expected_kinds or flanks != {"LeftFLANK", "RightFLANK"}:
            raise ValueError("Incomplete/nonalternating CRISPR region coverage")
        if parts[0][1] != start or parts[-1][2] != end or any(a[2] + 1 != b[1] for a, b in zip(parts, parts[1:])):
            raise ValueError("CRISPR array bounds or region continuity disagree")
        finding = _finding(contig, ident, "predicted CRISPR array", start, end, "array", array,
                           native_evidence_level=level, passes_threshold=level >= min_level,
                           repeat_count=spacers + 1, spacer_count=spacers, repeat_sequence=repeat,
                           repeat_intervals=[{"start": a, "end": b, "length": b - a + 1} for k, a, b in parts if k == "DR"])
        finding["strand"] = orientation
        if level < min_level:
            finding["evidence_level"] = "exploratory"
        findings.append(finding)
    return findings


def _native_table(path, no_hit):
    text = _read(path)
    lines = [line for line in text.splitlines() if line.strip()]
    if f"# macsyfinder {MACSYFINDER_VERSION}" not in lines or f"# models : CASFinder-{CAS_MODELS_VERSION}" not in lines:
        raise ValueError("Unverified MacSyFinder/model version in native results")
    data = [line for line in lines if not line.startswith("#")]
    if no_hit in lines:
        if data:
            raise ValueError("MacSyFinder zero-hit marker accompanies data")
        return []
    if not data:
        raise ValueError("MacSyFinder result has neither hits nor explicit no-hit marker")
    reader = csv.DictReader(io.StringIO("\n".join(data)), delimiter="\t")
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames) or not _MACSY_FIELDS.issubset(reader.fieldnames):
        raise ValueError("Malformed MacSyFinder result columns")
    rows = list(reader)
    if not rows or any(None in row or any(v is None for v in row.values()) for row in rows):
        raise ValueError("Malformed/empty MacSyFinder hit table")
    return rows


def _native_genes(native, safe_id, sequence):
    path = native / "GFF" / f"annotation_{safe_id}.gff"
    genes = {}
    for line in _read(path).splitlines():
        if not line or line.startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) != 9 or cells[0] != safe_id or cells[2] != "CDS":
            raise ValueError("Unexpected Prodigal annotation record")
        start, end = _interval({"Start": cells[3], "End": cells[4]}, len(sequence))
        strand = _strand(cells[6])
        attrs = dict(part.split("=", 1) for part in cells[8].split(";") if "=" in part)
        match = re.fullmatch(r"1_(\d+)", attrs.get("ID", ""))
        if not match:
            raise ValueError("Unsupported per-contig Prodigal gene ID")
        ident = f"{safe_id}_{match[1]}"
        if ident in genes or strand is None:
            raise ValueError("Duplicate/invalid Prodigal gene annotation")
        genes[ident] = (start, end, strand)
    return genes


def _verify_summary(path, safe_id, groups):
    text = _read(path)
    lines = text.splitlines()
    if f"# macsyfinder {MACSYFINDER_VERSION}" not in lines or f"# models : CASFinder-{CAS_MODELS_VERSION}" not in lines:
        raise ValueError("Unverified MacSyFinder summary versions")
    rows = list(csv.reader((line for line in lines if line.strip() and not line.startswith("#")), delimiter="\t"))
    if len(rows) != 2 or rows[0][0] != "replicon" or rows[1][0] != safe_id or len(rows[0]) < 2 or len(rows[0]) != len(rows[1]) or len(set(rows[0])) != len(rows[0]):
        raise ValueError("MacSyFinder summary does not cover exactly the input contig")
    counts = {model: _int(count, "Cas model count") for model, count in zip(rows[0][1:], rows[1][1:])}
    expected = Counter(value[0]["model_fqn"] for value in groups.values())
    if any(counts.get(model) != count for model, count in expected.items()) or any(count != expected.get(model, 0) for model, count in counts.items()):
        raise ValueError("MacSyFinder summary/hit counts disagree")


def _parse_cas(native, safe_id, contig, sequence, json_cas, stdout):
    if not isinstance(json_cas, list):
        raise ValueError("CRISPRCasFinder requires a Cas list")
    proteins = native / "Prodigal" / f"prodigal_{safe_id}" / f"{safe_id}.faa"
    if proteins.is_file() and not proteins.stat().st_size:
        if json_cas or "No Cas search because no CDS was detected!" not in stdout:
            raise ValueError("Empty native proteome lacks explicit skipped-Cas evidence")
        if _native_genes(native, safe_id, sequence):
            raise ValueError("Empty native proteome contradicts predicted CDS records")
        return [], {"status": "not_assessed_no_predicted_cds", "system_count": None, "candidate_model_count": None}
    genes = _native_genes(native, safe_id, sequence)
    proteome = read_fasta(proteins)
    if set(proteome) != set(genes):
        raise ValueError("Prodigal protein/GFF identifiers disagree")
    directory = native / "CasFinder" / f"casfinder_{safe_id}"
    log = _ANSI.sub("", _read(directory / "macsyfinder.log"))
    if _ERROR.search(log) or not re.search(r"(?:^|\s)END$", log.strip().splitlines()[-1]):
        raise ValueError("MacSyFinder did not record successful END completion")
    conf = configparser.ConfigParser(interpolation=None)
    conf.read_string(_read(directory / "macsyfinder.conf"))
    values = {key: value for section in conf.sections() for key, value in conf[section].items()}
    mode = values.get("db_type")
    expected_mode = "unordered" if len(sequence) < 100000 else "gembase"
    if mode != expected_mode or values.get("replicon_topology") != "circular":
        raise ValueError("MacSyFinder search mode differs from pinned CRISPRCasFinder behavior")
    source_db = Path(values.get("sequence_db", ""))
    if not source_db.name == f"{safe_id}.faa":
        raise ValueError("MacSyFinder configuration refers to an unexpected proteome")
    unordered = mode == "unordered"
    table = directory / ("all_systems.tsv" if unordered else "best_solution.tsv")
    rows = _native_table(table, "# No Likely Systems found" if unordered else "# No Systems found")
    groups, seen = defaultdict(list), set()
    for row in rows:
        ident = row["hit_id"]
        if ident not in genes or row["replicon"] != ("Unordered" if unordered else safe_id):
            raise ValueError("MacSyFinder hit does not belong to assessed contig/proteome")
        for key in ("gene_name", "model_fqn", "sys_id"):
            _name(row[key], key)
        if not row["model_fqn"].startswith("CASFinder/"):
            raise ValueError("MacSyFinder hit uses unexpected model family")
        key = (row["sys_id"], ident, row["gene_name"])
        if key in seen:
            raise ValueError("Duplicate Cas system component")
        seen.add(key)
        for key in ("hit_i_eval", "hit_score"):
            _number(row[key], key, minimum=-1e6 if key == "hit_score" else 0)
        for key in ("hit_profile_cov", "hit_seq_cov"):
            _number(row[key], key, maximum=1)
        length = _int(row["hit_seq_len"], "native protein length", 1)
        if length != len(proteome[ident].rstrip("*")) and length != len(proteome[ident]):
            raise ValueError("MacSyFinder protein length does not match native proteome")
        _interval({"Start": row["hit_begin_match"], "End": row["hit_end_match"]}, length)
        groups[row["sys_id"]].append(row)
    findings, incomplete_candidates = [], False
    if unordered:
        if json_cas:
            raise ValueError("Unexpected typed Cas systems in upstream unordered-mode JSON")
        # CCF 4.3.2 ignores all_systems.tsv in this mode; retain candidates explicitly.
        by_hit = defaultdict(list)
        for row in rows:
            by_hit[row["hit_id"]].append(row)
        for ident, supporting in by_hit.items():
            start, end, strand = genes[ident]
            finding = _finding(contig, ";".join(sorted({r["gene_name"] for r in supporting})),
                               "unordered Cas component candidate", start, end, "cas_component_candidate",
                               supporting, native_protein_id=ident, search_mode=mode,
                               candidate_models=sorted({r["model_fqn"] for r in supporting}),
                               typed_system_assessment="not_assessed_unordered_mode")
            finding["strand"] = strand
            findings.append(finding)
        incomplete = _read(directory / "uncomplete_systems.txt")
        markers = ["# No Unlikely Systems found", "# Unlikely Systems found:"]
        if sum(marker in incomplete.splitlines() for marker in markers) != 1:
            raise ValueError("Incomplete unordered Cas candidate output")
        incomplete_candidates = markers[1] in incomplete.splitlines()
    else:
        _verify_summary(directory / "best_solution_summary.tsv", safe_id, groups)
        indexed = {}
        for system in json_cas:
            if not isinstance(system, dict) or not isinstance(system.get("Genes"), list) or not system["Genes"]:
                raise ValueError("Malformed native Cas system")
            ident = _name(system.get("Type"), "Cas system ID")
            if ident in indexed:
                raise ValueError("Duplicate Cas system ID")
            indexed[ident] = system
        if set(indexed) != set(groups):
            raise ValueError("CRISPRCasFinder/MacSyFinder Cas systems disagree")
        for ident, supporting in groups.items():
            system = indexed[ident]
            start, end = _interval(system, len(sequence))
            expected = Counter((r["gene_name"], *genes[r["hit_id"]]) for r in supporting)
            actual = Counter()
            for gene in system["Genes"]:
                if not isinstance(gene, dict):
                    raise ValueError("Malformed Cas gene")
                a, b = _interval(gene, len(sequence))
                actual[(_name(gene.get("Sub_type"), "Cas gene type"), a, b, _strand(gene.get("Orientation")))] += 1
            if actual != expected or start != min(genes[r["hit_id"]][0] for r in supporting) or end != max(genes[r["hit_id"]][1] for r in supporting):
                raise ValueError("Cas system/gene coordinates disagree with native evidence")
            models = {row["model_fqn"] for row in supporting}
            if len(models) != 1:
                raise ValueError("Cas system has conflicting models")
            findings.append(_finding(contig, ident, next(iter(models)), start, end, "cas_system", system,
                                     search_mode=mode, native_components=supporting,
                                     native_protein_ids=[r["hit_id"] for r in supporting]))
    return findings, {"status": "completed", "mode": mode,
                      "system_count": None if unordered else len(groups),
                      "candidate_model_count": len(groups) if unordered else 0,
                      "component_count": len({r["hit_id"] for r in rows}),
                      "unordered_incomplete_candidates_present": incomplete_candidates,
                      "completion_check": "native_log_END_configuration_and_result_reconciliation"}


def parse_contig(native, safe_id, contig, sequence, stdout, min_evidence_level=3):
    """Validate one complete per-contig invocation; not a generic JSON importer."""
    native = Path(native)
    stdout = _ANSI.sub("", stdout)
    if _ERROR.search(stdout) or "Thank you for using CRISPRCasFinder.pl!" not in stdout:
        raise ValueError("CRISPRCasFinder lacks successful completion or reported an error")
    data = json.loads(_read(native / "result.json"))
    if not isinstance(data, dict) or data.get("Version") != VERSION or not isinstance(data.get("Sequences"), list) or len(data["Sequences"]) != 1:
        raise ValueError("Unsupported/incomplete CRISPRCasFinder result schema")
    record = data["Sequences"][0]
    if not isinstance(record, dict) or record.get("Id") != safe_id or record.get("Version") != safe_id or _int(record.get("Length"), "sequence length", 1) != len(sequence):
        raise ValueError("CRISPRCasFinder sequence identity/length mismatch")
    saved = read_fasta(native / "rawFASTA" / "analyzedSequences" / f"{safe_id}.fna")
    if saved != {safe_id: sequence}:
        raise ValueError("CRISPRCasFinder analyzed sequence differs from staged input")
    # Upstream constructs Command after GetOptions consumes its flags. The
    # checked adapter invocation and native Cas artifacts establish requested scope.
    arrays = _parse_arrays(record.get("Crisprs"), contig, sequence, min_evidence_level)
    counts = re.findall(r"(?m)^All CRISPRs = (\d+)\s*$", stdout)
    if counts != [str(len(arrays))]:
        raise ValueError("CRISPRCasFinder stdout/JSON array counts disagree")
    cas, status = _parse_cas(native, safe_id, contig, sequence, record.get("Cas"), stdout)
    return arrays + cas, status


def run(module, sample, config, outdir, runner):
    if module != "crisprcasfinder":
        raise ValueError(f"Unsupported CRISPRCasFinder module: {module}")
    root = Path(config.get("database") or "").resolve()
    script = root / "CRISPRCasFinder.pl"
    if not script.is_file() or hashlib.sha256(script.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("CRISPRCasFinder source must match pinned release 4.3.2 SHA256")
    library = root / "sel392v2.so"
    models = root / "models" / "CASFinder"
    if not library.is_file() or not library.stat().st_size:
        raise ValueError("CRISPRCasFinder requires its prepared sel392v2.so library")
    metadata = yaml.safe_load(_read(models / "metadata.yml"))
    if not isinstance(metadata, dict) or str(metadata.get("vers")) != CAS_MODELS_VERSION or not any((models / "profiles").glob("*.hmm")) or not any((models / "definitions").rglob("*.xml")):
        raise ValueError("CRISPRCasFinder requires prepared CASFinder 3.1.0 models")
    sequences = read_fasta(sample["assembly"])
    if any(not _BASES.fullmatch(s) for s in sequences.values()):
        raise ValueError("CRISPRCasFinder input contains unsupported DNA characters")
    threads = _int(config.get("threads", 1), "threads", 1)
    level = _int(config.get("min_evidence_level", 3), "minimum array evidence level", 1)
    if level > 4:
        raise ValueError("Minimum CRISPR evidence level must be 1..4")
    code = _int(config.get("genetic_code", 11), "genetic code", 1)
    outdir = Path(outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    native = outdir / "native"
    native.mkdir(exist_ok=False)
    mapping, findings, statuses = {}, [], {}
    map_path = outdir / "contig_map.json"
    for index, (contig, sequence) in enumerate(sequences.items(), 1):
        safe_id = f"CCF{index:06d}"
        # CCF builds unquoted shell commands using getcwd(). A safe temporary
        # directory also supports studies/database paths containing whitespace.
        work = Path(tempfile.mkdtemp(prefix="basis-ccf-", dir="/tmp"))
        mapping[safe_id] = {"contig": contig, "length": len(sequence),
                            "sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
                            "execution_workdir": str(work), "artifacts_relocated_to": str(native / safe_id)}
        map_path.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
        try:
            shutil.copyfile(script, work / "CRISPRCasFinder.pl")
            for license_name in ("COPYING", "COPYRIGHT"):
                if (root / license_name).is_file():
                    shutil.copyfile(root / license_name, work / license_name)
            (work / "sel392v2.so").symlink_to(library)
            (work / "models").symlink_to(models.parent, target_is_directory=True)
            (work / "input.fna").write_text(f">{safe_id}\n{sequence}\n", encoding="ascii")
            # Project config masks user-wide model locations and keeps paths shell-safe.
            (work / "macsyfinder.conf").write_text("[directories]\nmodels_dir = models\n", encoding="ascii")
            (work / "bin").mkdir()
            shim = work / "bin" / "macsyfinder"
            shim.write_text(_MACSY_SHIM, encoding="utf-8")
            shim.chmod(0o700)
            (work / "basis_ccf_launch.py").write_text(_CCF_LAUNCHER, encoding="utf-8")
            stdout_path = work / "stdout.txt"
            logfile = getattr(runner, "logfile", None)
            log_offset = Path(logfile).stat().st_size if logfile and Path(logfile).exists() else 0
            result = runner.run(["python", "basis_ccf_launch.py", config.get("executable", "perl"), "CRISPRCasFinder.pl", "-in", "input.fna",
                                 "-out", "native", "-so", "./sel392v2.so", "-cas", "-keep", "-log",
                                 "-cpuM", str(threads), "-gcode", str(code)], cwd=work, stdout_path=stdout_path)
            if result.returncode != 0:
                raise ValueError("CRISPRCasFinder native command failed")
            if logfile and Path(logfile).exists():
                with Path(logfile).open("rb") as handle:
                    handle.seek(log_offset)
                    logged = _ANSI.sub("", handle.read().decode("utf-8", errors="replace"))
                if _ERROR.search(logged):
                    raise ValueError("CRISPRCasFinder command log records a native error")
            rows, status = parse_contig(work / "native", safe_id, contig, sequence, _read(stdout_path), level)
            _verify_runtime_isolation(work, status)
        finally:
            # Preserve diagnostics on failure as well as successful native outputs.
            shutil.move(str(work), native / safe_id)
        findings.extend(rows)
        statuses[contig] = status
    arrays = [f for f in findings if f["attributes"]["feature_type"] == "array"]
    complete = all(s["status"] == "completed" for s in statuses.values())
    return {"findings": findings, "artifacts": [str(native), str(map_path)],
            "interpretation": "detected" if findings else ("not_detected" if complete else "inconclusive"),
            "metrics": {"crisprcasfinder_version": VERSION, "source_sha256": SOURCE_SHA256,
                        "macsyfinder_version": MACSYFINDER_VERSION, "casfinder_models_version": CAS_MODELS_VERSION,
                        "array_count": len(arrays), "array_count_at_reporting_level": sum(f["attributes"]["passes_threshold"] for f in arrays),
                        "repeat_count": sum(f["attributes"]["repeat_count"] for f in arrays),
                        "spacer_count": sum(f["attributes"]["spacer_count"] for f in arrays),
                        "cas_system_count": (sum(s["system_count"] or 0 for s in statuses.values())
                                             if any(s.get("mode") == "gembase" for s in statuses.values()) else None),
                        "typed_cas_assessed_contig_count": sum(s.get("mode") == "gembase" for s in statuses.values()),
                        "cas_unordered_candidate_model_count": sum(s["candidate_model_count"] or 0 for s in statuses.values()),
                        "cas_assessment": "completed" if complete else "partially_assessed",
                        "cas_contig_assessments": statuses, "assessed_contigs": list(sequences),
                        "assessed_contig_count": len(sequences), "genetic_code": code,
                        "completion_check": "all_contig_JSON_native_Cas_artifacts_and_coordinates_verified"},
            "notes": ["CRISPR/Cas predictions describe genomic context, not activity, pathogenicity or organism safety.",
                      "Pinned CRISPRCasFinder assumes circular topology for MacSyFinder; this is an algorithm setting, not confirmed assembly topology.",
                      "Contigs shorter than 100 kb use upstream unordered Cas search. Component candidates from native MacSyFinder output are retained; typed systems are not assessed for these contigs.",
                      "Rejected/incomplete Cas candidates remain in the native MacSyFinder artifacts and do not establish a typed system.",
                      "Saved run-local wrappers suppress ambient MacSyFinder system/user configuration; pinned defaults, model configuration and explicit native settings remain active. HOME is unchanged.",
                      "Prodigal predicts Cas-search proteins independently; native protein IDs are not asserted to match the submitted annotation.",
                      "No predicted CDS means Cas was not assessed, not that Cas genes are absent."]}
