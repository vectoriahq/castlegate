# IIS setup for package 1's second part

The PowerShell scripts that set up the test server for package 1's second part, in the order they ran on 2026-11-04 on Windows Server 2022, through Azure's run-command as the local system account. Groundwork's plan, `docs/plans/qgis-server-access-control.md` § "Package 1, second part: findings, 2026-11-04", records what they found. They are the starting point for package 2's install script, not that script.

- `1-install-iis-and-qgis.ps1` adds IIS with CGI and installs OSGeo4W's `qgis-ltr-server` package unattended.
- `2-pools-and-prototype.ps1` expects `C:\setup\pkg.zip` holding `castlegate_spike/`, `rules.json` and `build_project.py` (with `OUT` read from `CASTLEGATE_OUT`). It builds the test project, then creates the two pools, the two FastCGI entries for the same program, the two applications and their handler mappings. Replace `<tenant id>` and `<app registration id>` first.
- `3-cors-module-and-preflight.ps1` installs Microsoft's IIS CORS module, writes the CORS rule for Groundwork's origin into Groundwork's application, and checks the preflight.
- `4-install-connector.ps1` installs the Entra private network connector without registering it. Registering it with a device-code token failed; the installer's own sign-in, run on the desktop, worked.
