# Installation

Python 3.9 or newer is required; offline tests were verified with Python 3.9.6. The instrument must be reachable over TCP. VXI-11 uses port mapper port 111 and a negotiated core port. The VISA resource is `TCPIP::<host>::inst0::INSTR`.

## From the project directory

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .              # pyvisa, pyvisa-py, psutil, zeroconf
pip install -e ".[plot]"      # numpy and matplotlib for NPZ/PNG
```

Alternatively, use `pip install -r requirements.txt`. For development, use `pip install -r requirements-dev.txt`, then set `PYTHONPATH=src` if the package is not installed.

## Running

```bash
mp72-control --host 192.0.2.10 status
python -m mp72_control --host 192.0.2.10 status
```

`192.0.2.10` is an example from the documentation address range. Replace it with the instrument address. There is no default value for `--host`.

## Without PyVISA

`--transport raw-vxi11` uses the built-in dependency-free transport. NPZ and PNG output still require NumPy and Matplotlib.

## Offline verification

```bash
python3 -m pytest -q
python3 -m py_compile src/mp72_control/*.py examples/*.py tests/*.py
```

Tests run without installing the package because they add `src` to `sys.path`.
