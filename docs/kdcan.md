# Using a K+DCAN cable

E92-Redline talks to the car through an ordinary K+DCAN USB cable (Bimmergeeks,
or any FTDI FT232R-based "INPA" cable). No CAN adapter, no drivers to hunt down,
and the same cable works on Linux, macOS and Windows.

## How it works

The cable is a USB serial port with a small microcontroller inside. In D-CAN
mode (every E9x built from 2007, so every E92 M3) it accepts BMW-FAST telegrams
at 115200 baud and relays them onto the car's diagnostic CAN bus at 500 kbit/s:

```
[laptop] --USB serial, 115200 8N1--> [K+DCAN cable] --D-CAN, 500 kbit/s--> [DME 0x12]
          82 12 F1 01 0C 92                          ISO-TP 0x6F1 / 0x612
```

* Telegram: `[0x80|len] [target] [F1] [payload] [8-bit sum]`
* The cable echoes what you send; E92-Redline strips the echo (and copes with cables that don't)
* DSR on the cable reports ignition, so the app can tell you the key is off
* Live Data asks the DME for standard OBD-II Mode 01 values; the first time it
  works out which address the DME answers on and remembers it

## Setup per operating system

### Linux

The cable appears as `/dev/ttyUSB0` using the kernel's built-in `ftdi_sio` driver.

```bash
sudo usermod -aG dialout $USER     # once, then log out and back in
```

**Faster live data.** FTDI chips hold received bytes for up to 16 ms by default,
which caps how many values per second you get. E92-Redline asks for 1 ms
automatically; to make it stick for every program, add a udev rule:

```bash
echo 'ACTION=="add", SUBSYSTEM=="usb-serial", DRIVER=="ftdi_sio", ATTR{latency_timer}="1"' \
  | sudo tee /etc/udev/rules.d/99-ftdi-latency.rules
sudo udevadm control --reload-rules   # then replug the cable
```

### macOS

Nothing to install: macOS includes an FTDI driver. The cable appears as
`/dev/cu.usbserial-XXXXXXXX` (E92-Redline lists the `cu.` port and hides its
`tty.` twin, which blocks on open). Install Python 3.11+ from python.org or Homebrew.

### Windows

Windows Update installs the FTDI driver the first time you plug the cable in.
It appears as `COM3` (or similar) under **Device Manager → Ports (COM & LPT)**.

**Faster live data:** Device Manager → the cable's COM port → Properties →
Port Settings → Advanced → **Latency Timer (msec) = 1**.

If you've used INPA/EDIABAS, they often ask you to move the cable to COM1;
that doesn't matter here. Close INPA, ISTA or the Bimmergeeks app first,
since Windows lets only one program open the port.

## Check the cable

With the cable in the OBD port and the ignition on:

```bash
e92-redline-cablecheck            # picks the most likely port
e92-redline-cablecheck COM3       # or name it
e92-redline-cablecheck /dev/ttyUSB0 --try-lines --debug
```

It reports whether the port opens, ignition state, whether the DME answers, which
address OBD-II requests go to, RPM and coolant, and how many requests per second
the cable manages. `--debug` prints every byte, which is the thing to paste into
an issue if something doesn't work. The Connect page's **Test Adapter** button
runs the same check.

## Developing without the car

Two simulators, both answering with a sweeping RPM and plausible values:

```bash
# Any OS: built-in simulated cable, listed on the Connect page
e92-redline --simulate

# Linux/macOS: simulated cable on a real pseudo-terminal, exercising pyserial too
python tools/fake_kdcan.py            # prints e.g. /dev/pts/4
e92-redline --port /dev/pts/4
```

`--port` also adds any port the automatic scan missed.

## Known limits

* Only values the DME reports over standard OBD-II are shown. S65-specific data
  (per-cylinder misfire counters, VANOS positions, knock retard per cylinder)
  lives behind BMW's own MSS60 identifiers and is the next step.
* Cables with a K-line/D-CAN switch must be set to D-CAN for the E92.
