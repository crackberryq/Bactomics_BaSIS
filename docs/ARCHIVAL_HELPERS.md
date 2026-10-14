# Historical development helpers

The supported public entry point is `basis`; see the repository README and
`docs/GITHUB_QUICKSTART.md`. Root `BaSIS_*.py` helpers originated during development
and some expect the original pilot PC, saved studies or frozen audit pins.
Those private records are not included in this public source repository.

In particular, `BaSIS_check_normalization.py` targets the frozen rc13 pilot
checkpoint. It is retained for its synthetic helper tests, not as an acceptance
command for this release. `BaSIS_check_interface.py` is the portable synthetic
interface check described in the current quickstart.

The rc13 and rc14 release archives and their recorded audit identities remain
separate. This repository does not claim to contain their complete native run
artifacts, environment captures, databases, model weights or input genomes.
