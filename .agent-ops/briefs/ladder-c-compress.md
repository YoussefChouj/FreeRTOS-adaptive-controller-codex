# Research: compress a fixed-schema telemetry stream on a Cortex-M4 (read-only: web + academic + this repo)

Context: an indoor quadrotor, STM32F407 (Cortex-M4F, 168 MHz, about 44 KB free RAM). It streams up to 4 "slots" of
logged variables over USART3 at 921600 baud to a WiFi bridge (about 91 kB/s wire ceiling; 55 kB/s measured, 0% loss).
The schema is sent once; each data frame is 12 B of overhead plus raw values (mostly float32, some uint8/int16/uint32),
up to 62 contiguous memory ranges per slot, about 142 variables in total at 100 Hz and 25 Hz. Frame layout:
`API/subscribe.h:124-145` (per-slot SEQ byte, T_MS, CRC16). The host decoder is Python: `ground_station/livewatch/stream.py`
around line 520-650. A bad CRC drops the frame and a SEQ gap counts a lost frame, so frames are lost whole, never partly.
The plan is to send narrower types first (int16 with a per-variable scale) and then decide on delta compression.

Part 1. Adopt or build? For each candidate give: how it encodes (keyframe/delta, predictor, integer packing), the
licence, the compression ratio it reports on real flight or sensor data, the CPU cost on a small MCU if known, and one
quote under 15 words with a URL. Verify these and add at most 2 more:
- Betaflight / Cleanflight blackbox: I-frames and P-frames, predictors, signed variable-byte and tag8_4S16 /
  tag2_3S32 group encodings.
- PX4 ULog: does it compress at all, or only log raw structs?
- Gorilla (Facebook, VLDB 2015): delta-of-delta timestamps and XOR float values.
- Sprintz (Blalock et al., 2018): time-series compression designed for microcontrollers.

Part 2. Loss. Our link drops whole frames. With delta frames, one lost frame breaks every frame until the next keyframe.
What keyframe interval, or what scheme (delta against the last keyframe only, not the last frame), do these systems use,
and what does it cost in ratio? How does Betaflight's decoder recover after a corrupt frame?

Part 3. Which order is best for us: (a) int16 quantize then delta + zigzag + varint, (b) XOR floats on raw float32,
(c) Betaflight-style tag group encodings on quantized ints. Give the expected ratio over raw float32 for each, as sourced
numbers or marked UNVERIFIED.

Answer in at most 40 lines. Mark anything you could not source as UNVERIFIED. End with a one-line verdict: adopt which
encoder (and its licence fit for a project with no licence file), or build which minimal scheme.
