# Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| `pyvisa is not installed` | dependencies are missing | `pip install -r requirements.txt` or use `--transport raw-vxi11` |
| `failed to open VXI-11 instrument` | wrong address, network, or firewall | verify reachability, port 111, and the instrument address |
| `ERROR: ... timed out` during `waveform` | channel is disabled or instrument is busy | enable it with `channel-set N --on`; the library rejects disabled channels before transfer |
| failures after timeout | the VXI-11 request/response stream is poisoned | close the link and start a fresh command; never interpret the next response |
| `create_link failed with device error 3` | another owner or unreleased link slot | stop the owner gracefully, wait for `destroy_link`, then retry once |
| `binary response is truncated` | transport cannot complete the block | use PyVISA or the built-in raw VXI-11 transport |
| returned points differ from requested points | firmware ignores `:WAVeform:POINts` | use `returned_points`; capture under STOP for repeatability |
| `acquire --mode` cannot report mode | `:ACQuire:MODE?` times out | this is a firmware limitation |
| measurement returned `*` | the current signal cannot produce the value | check signal, scale, and trigger |
| `trigger level mismatch` | instrument clamped or rejected level | check source/scale; retry once at most; do not claim restoration |
| `memory depth mismatch` | firmware rejected requested depth | keep and report the readback value |
| AUTO depth warning | instrument reports a concrete depth such as 28K | record WARN; this can be legitimate firmware behavior |
| average-count mismatch | count is accepted only under AVERAGE and still differed | check acquisition setup and retry once |
| `invalid ...; allowed: [...]` | input is outside the allowlist | select one of the reported values |
| NPZ/PNG `ModuleNotFoundError` | NumPy or Matplotlib is missing | `pip install -e ".[plot]"` |
| converted voltages look wrong | calibration is provisional | use `--raw-only` and compare with a calibrated source |
| `:DISPlay:GRID:MODE?` timeout | documented query fails on 5.00.0.19 | do not write grid mode without a baseline |
| FILTER high cutoff differs | firmware clamp or mode dependency | preserve cutoffs by default; explicit mismatch is failure |
| AUTO becomes 28K after FFT/MATH/FILTER | hidden FUNCtion side effect | guard and restore memory depth |
| screensaver creates BMP files | `--retain-screens N` is greater than zero | omit it or set `--retain-screens 0` |
| cursor log has no numeric delta | manual has no numeric cursor-result query | read the panel; remote evidence covers type/mode/source/positions only |
| cursor frame has WARN with correct readback | lines changed too few pixels | inspect the display and `diff_ratio`; run a short live smoke test if repeated |
| cursors remain after a crash | SIGKILL or power loss skipped cleanup | after the old owner exits, run `cursor_demo.py --restore .../recovery.json` |
| cursor MODE?/SOURCE? timeout | unavailable while TYPE=CLOSE on this firmware | use partial recovery, temporary TIME, hidden-field read, then CLOSE |
| CURA moves after CURB in TRACK | hardware couples positions differently by type | never write A/B in TRACK; use INDEPENDENT for separated positions |
| cursor demo reports no enabled channel | CH1-CH4 are all off | deliberately enable the required channel; the cursor demo never does so |
| cinematic show rejects non-EDGE trigger | typed API cannot restore other types | select EDGE manually or use the visual/cursor demo |
| cinematic show reports `SENT` | fast mode intentionally omits per-frame proof | expected; SENT proves delivery only, while Ctrl-C still verifies restoration |
| channel visibility remains after a crash | cinematic recovery was interrupted | after the owner exits, run `cinematic_show.py --restore .../recovery.json` |

For unstable RUN point counts, issue `stop`, capture, then `run`; remember that RUN/STOP state has no readback.

If a failed capture leaves the instrument unresponsive, a fresh connection usually restores communication. The project intentionally exposes no reset command.

See [OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md) for the complete operating and recovery workflow.
