# INSPIRE 2 control protocol (reverse-engineered)

Source: Revo Metro 5.8.8 `3DCamera.dll` + a USBPcap capture of real traffic
(`capture/inspire2.pcapng`).

## Transport
- USB VID:PID `2207:110c`. HID control interface (IF4): interrupt EP 0x01 OUT
  + EP 0x03 IN, 1024-byte reports.
- Control report framing:
  - magic `5a5a5a5a` ("ZZZZ")
  - `01010010` (command type)
  - 32 bytes context (zeros OK)
  - 4-byte little-endian payload length
  - ASCII command
- Device responds on EP 0x03 IN with `5a5a5a5a 0605 0100` status packets.

## Commands
All commands are register writes to the Rockchip pre-ISP:
`echo s 0xNNN <val> >/dev/rk_preisp`

| Register | Meaning (observed) |
|----------|--------------------|
| 0x481 = 1 | depth engine ON |
| 0x702 640 512 | depth resolution |
| 0x910 / 0x911 | exposure |
| 0x483 | init |
| 0x707 200 600 | init |
| 0x904 0 | init |
| 0x903 0x10 | init |
| 0x488 0 / 0x48b 10 / 0xb03 0 | init |

## Init sequence (order matters)
```
echo s 0x483 >/dev/rk_preisp
echo s 0x707 200 600 >/dev/rk_preisp
echo s 0x904 0 >/dev/rk_preisp
echo s 0x702 640 512 > /dev/rk_preisp
echo s 0x903 0x10 > /dev/rk_preisp
echo s 0x910 7000 >/dev/rk_preisp
echo s 0x911 5000 > /dev/rk_preisp
echo s 0x488 0 > /dev/rk_preisp
echo s 0x48b 10 > /dev/rk_preisp
echo s 0xb03 0 > /dev/rk_preisp
echo s 0x481 1 >/dev/rk_preisp   # depth ON
```

## Depth stream
- Depth cam `REVO_PRODUCT: INSPIRE2-REVO-Dep`, Y16 640x512 @15fps.
- Arming the depth engine causes the device to re-enumerate (node numbers
  change) — re-find nodes after arming.
- Depth map is empty when no object is in range.

## Register reality check (Nov 2026)
Writing any register beyond the exact vendor init sequence breaks the laser.
Tested both ways — poked into a live stream and baked into a fresh init before
arming — every extra knob collapsed depth from 8.57% to ~0.06% fill (columns
630 -> 57): AE mode `0x912`, HDR `0x914`, laser line mode `0xb07`, gain `0x903`,
exposure `0x911`. The scanner only exposes registers; the auto-exposure /
option logic lives in the vendor app (RPALaserAutoExposure). `0x914` also
forces a device re-enumeration.

## Stream layout
The 640x512 Y16 buffer is mostly padding — real depth data occupies a 640x128
band, rows 0-117. `1920x512` is the 640x512 frame tiled 3x; `640x1024` is a
static pattern. Depth values read near-constant across a frame-filling subject,
i.e. this stream is the *input* to RPALaserRecon, not a finished depth map.

## Notes
- The JSON-RPC messages (`queryCameras`, `connect`, `startStream`, ...) ride a
  different channel (network/adb), not the HID. The HID carries the register
  writes + status.
- Clean-room: protocol facts only, no vendor code redistributed.
