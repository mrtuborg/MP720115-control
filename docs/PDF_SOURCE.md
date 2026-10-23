# PDF guide source and structure

Build the guide with:

```bash
python tools/build_pdf.py
```

The output is `output/pdf/mp72-control-guide.pdf`. Generated PDFs are intentionally ignored by Git. The builder uses only project materials and does not contact the oscilloscope or the network.

## Source order

1. `README.md` - overview, safety, and quick start.
2. `docs/INSTALLATION.md`.
3. `docs/COMMAND_REFERENCE.md`.
4. `docs/ARCHITECTURE.md`.
5. `docs/VALIDATION.md` - live verification, documented-only behavior, and firmware observations.
6. `docs/TROUBLESHOOTING.md`.
7. `docs/OPERATOR_RUNBOOK.md`.

## Illustrations and data

- `docs/examples/live_screen_test.png` - an 800x480 screen capture from the instrument.
- `docs/examples/live_waveform_ch1.csv` - a CH1 waveform with columns `index,time_s,raw_code,volts_provisional`; voltage remains provisional.
- The original BMP was not copied into the repository.

## Required qualifications

- Official manual: <https://unitrend.oss-cn-hongkong.aliyuncs.com/uploads/attach/20250624/171046f30ffda67ff849c78002191.pdf>.
- Voltage conversion `(raw - yref) * yinc - yorigin` is provisional.
- The tested instrument runs firmware `5.00.0.19`; other versions have not been characterized.
- Do not describe the instrument as a confirmed UNI-T UPO3000E. Only the SCPI dialect match has been demonstrated.

## Guide contents

1. Purpose and confirmed result.
2. Safety and limitations.
3. Installation on macOS/Linux.
4. Quick start and the normal configure/capture workflow.
5. CLI and programming API reference.
6. Waveform format, IEEE 488.2 blocks, and coordinate conversion.
7. Firmware `5.00.0.19` observations.
8. Architecture and state restoration.
9. Visual demonstrations: display/FUNCtion screensaver, measurement cursors, fast cinematic show, dwell behavior, in-memory verification, and recovery.
10. Troubleshooting.
11. Live-validation matrix, illustrations, and sources.
