$env:PYTHONPATH = Split-Path $PSScriptRoot -Parent
python -m ase @args
exit $LASTEXITCODE
