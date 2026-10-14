# Publishing BaSIS

- Repository: https://github.com/crackberryq/Bactomics_BaSIS
- Distribution: `bactromics-basis`; command: `basis`
- Author: Jimi Goldstein; ORCID: https://orcid.org/0000-0002-7829-0090
- License: MIT for BaSIS; third-party tools, data and model weights retain their own terms

The source supports installation directly from GitHub. **PyPI and TestPyPI publication are separate actions and remain pending until their uploads and installation checks succeed.** The prepared candidate is `0.2.0rc15`.

## Build and verify

From a clean checkout with Python 3.11+:

```bash
python -m pip install setuptools wheel twine '.[ui,excel]'
python -m unittest discover -s tests
python scripts/build_release.py
python -m twine check dist/*
```

The build script seals the bundled runtime manifest and builds wheel/sdist artifacts. CI runs on Ubuntu with Python 3.11 and 3.12, then installs the built wheel and verifies it from outside the checkout. Review all test failures and skips, artifact hashes and the exact source commit. Build success does not validate native tools or biological accuracy.

For a release, use a clean output directory, retain checksums and tie the artifacts to an immutable source commit/tag. Confirm the license, author metadata, version and citation exports agree. A deployment ZIP with an installer is a separate artifact from the source archive and wheel; it needs its own manifest.

## Configure the package index

Confirm project-name availability and ownership on TestPyPI/PyPI. Prefer Trusted Publishing, with the exact repository, workflow filename and GitHub environment registered in the package index. The existing `publish.yml` workflow builds artifacts by default; its explicit `publish_testpypi` option uploads only to TestPyPI after publisher configuration. Production PyPI publication needs its own reviewed publisher/release setup.

Use an environment approval policy appropriate to the repository. Keep credentials out of source, logs, reports and release bundles. Official instructions:
https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/

After an actual upload, install the exact published version in a new environment, verify the package and run the synthetic interface/CLI checks. Record which index supplied the package and preserve the installation artifacts. A future PyPI installation would use:

```bash
python -m pip install 'bactromics-basis[ui,excel]==0.2.0rc15'
```

That is a publication target, not evidence that this version is already on PyPI.

## Release scope

Keep native tool environments, databases, reference sets and model weights separate, with their versions, checksums and license terms. Do not distribute private samples, pilot results, local credentials, tool environments or mutable caches in the public source release. The source repository intentionally omits historical validation/reproducibility archives. Retain identified audit artifacts separately where collaboration requires them.

The interface, synthetic tests and pilot replay checks support software acceptance. Independent biological validation and clean-machine native replication retain their own evidence and status.
