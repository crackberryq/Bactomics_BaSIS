# Isolated Python-launcher restoration

`BaSIS_check_clean_restore.py` checks the deployable Python launcher in a new venv, without borrowing site-packages from the current environment. It retains package wheels and their SHA256 digests, checks that installed imports resolve inside that venv, runs `pip check`, and exercises only synthetic BaSIS installation/reporting behavior.

The first invocation stages exact PyYAML, openpyxl and et-xmlfile versions from PyPI over TLS. Versions alone are not artifact identities: the resulting wheelhouse lock records the exact filenames and bytes for the platform used. The bundled BaSIS wheel is checked against the delivered release manifest. The subsequent installation uses only these local wheels, with no dependency resolution or network index.

For an offline repeat, supply both the prior wheelhouse and its independently retained lock SHA256. The helper checks the lock and every wheel before creating the new environment. It never deletes or overwrites an existing output or environment. A first acquisition still relies on the package source and transport; recorded hashes do not by themselves establish third-party authorship.

The Python executable, standard library, operating system and hardware still come from the host. A pass is therefore an isolated launcher restoration on that host, not a new-operating-system test or a full native scientific workflow restore. GPU capability, external native-tool packages, databases/models, licences and scientific output repeatability remain separate gates. Preserve their source bytes and evaluate those gates on the intended validation host.

## Evidence to retain

Keep `restore-summary.json`, the compact summary ZIP, command logs, wheelhouse, wheelhouse lock and its separately recorded SHA256. For an offline installation on another machine, transfer compatible wheels plus the lock; the compact ZIP does not include wheel bytes. Record both the original acquisition and later offline-repeat results. Do not substitute a newly generated lock for a failed checksum.
