# Offline Python dependency installation

`ops\setup.ps1` defaults to `Wheelhouse` mode because `files.pythonhosted.org` may be unavailable. The mode verifies `ops\ui-auto-wheelhouse.zip`, extracts it to a temporary directory, creates a Python 3.12 virtual environment, installs the exact versions from `requirements.lock.txt` without consulting a package index, and removes the extracted directory.

The wheel archive contains Python packages, not the Python interpreter. If Python 3.12 is missing, setup asks uv to install its managed Python before creating the environment; this download does not use `files.pythonhosted.org`.

Run the default offline setup for a local checkout:

```powershell
.\ops\setup.ps1
```

Runner onboarding invokes the same script with the shared CI environment:

```powershell
.\ops\setup.ps1 -EnvironmentPath 'C:\uv-venvs\ui-automation'
```

When direct package-index access is available, select online mode explicitly:

```powershell
.\ops\setup.ps1 -DependencyMode Online
```

There is no extracted repository-local wheel directory. Setup extracts `ops\ui-auto-wheelhouse.zip` under the system temporary path and deletes that extraction after installation. Keep the archive available for initial setup, repair, or rebuilding a restored DevBox baseline.
