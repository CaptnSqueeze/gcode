================================================================================================================
GENERAL NOTES
================================================================================================================
- 100µF capacitor across VM and GND on EVERY TMC2209 — solder close to the driver. Non-negotiable.
- 1000µF capacitor across PSU+ and GND near the power supply output terminals.
- 1000µF capacitor across buck converter output (5V side), as close to the output as possible.
- Common ground — PSU-, ESP32 GND, and all TMC2209 GNDs must be connected together.
- VIO = 3.3V only — logic level for TMC2209. Do NOT give it 5V.
- Standalone mode — no UART. Microstepping set via onboard pads/jumpers.
- All motor connections use 4-pin connectors for modularity. Extend with female-to-female 4-pin cables.
- Motor coil pairs: measure with multimeter if unlabeled (continuity between coil 1 wires, continuity between coil 2 wires).
- EN pins on all TMC2209s are wired to GND — drivers are always enabled when powered.
- WiFi is in use — see GPIO avoidance list below.

TEMPORARY: Motor 5 (Z axis) is driven by a TB6600 external driver instead of a TMC2209.
Replace with TMC2209 when replacement stock arrives.

================================================================================================================
POWER
================================================================================================================
Component                Connect To                  Notes
---------                ----------                  -----
PSU +                    TMC2209 x4 VM pins          Red — star or daisy chain with thick wire
PSU +                    TB6600 VCC (motor power)    Red — TB6600 accepts 9–42V, same PSU rail is fine
PSU +                    Buck converter IN+           Red
PSU -                    Common GND bus              Black — all grounds connect here
Buck converter IN-       Common GND                  Black
Buck converter OUT+      ESP32 VIN or 5V pin         Set to 5V, verify with multimeter BEFORE connecting ESP32
Buck converter OUT-      Common GND                  Black
TB6600 GND               Common GND                  Black

Capacitors:
1000µF across PSU+ and GND              Place at PSU output terminals
1000µF across buck OUT+ and GND         Place as close to buck output as possible, before the ESP32
100µF across VM and GND                 One per TMC2209 (x4), soldered close to each chip
No decoupling cap needed on TB6600      It has its own onboard filtering

================================================================================================================
GPIO — PINS TO AVOID
================================================================================================================
GPIO 0        Boot mode — avoid
GPIO 1        UART TX — keep free for debug
GPIO 2        Boot pin — avoid
GPIO 3        UART RX — keep free for debug
GPIO 6–11     Internal flash — never use
GPIO 12       Boot-sensitive — avoid
GPIO 15       Boot/JTAG — avoid
GPIO 16/17    PSRAM conflict on some boards — avoid
GPIO 34/35    Input only — no output
GPIO 36/39    Input only — no output

================================================================================================================
TMC2209 #1 — NEMA 17 (Motor 1)
================================================================================================================
TMC2209 Pin          Connect To              
-----------          ----------              
VM                   PSU +                   100µF cap across VM and GND, close to chip
GND                  Common GND              
VIO                  ESP32 3.3V              
GND                  Common GND              
STEP                 ESP32 GPIO 27           
DIR                  ESP32 GPIO 26           
EN                   GND                     

Motor (4-pin connector):
Pin 1 (1A)           Motor coil 1 wire 1
Pin 2 (1B)           Motor coil 1 wire 2
Pin 3 (2A)           Motor coil 2 wire 1
Pin 4 (2B)           Motor coil 2 wire 2

================================================================================================================
TMC2209 #2 — NEMA 17 (Motor 2)
================================================================================================================
TMC2209 Pin          Connect To              
-----------          ----------              
VM                   PSU +                   100µF cap across VM and GND, close to chip
GND                  Common GND              
VIO                  ESP32 3.3V              
GND                  Common GND              
STEP                 ESP32 GPIO 18           
DIR                  ESP32 GPIO 19           
EN                   GND                     

Motor (4-pin connector):
Pin 1 (1A)           Motor coil 1 wire 1
Pin 2 (1B)           Motor coil 1 wire 2
Pin 3 (2A)           Motor coil 2 wire 1
Pin 4 (2B)           Motor coil 2 wire 2

================================================================================================================
TMC2209 #3 — NEMA 17 (Motor 3)
================================================================================================================
TMC2209 Pin          Connect To              
-----------          ----------              
VM                   PSU +                   100µF cap across VM and GND, close to chip
GND                  Common GND              
VIO                  ESP32 3.3V              
GND                  Common GND              
STEP                 ESP32 GPIO 14           
DIR                  ESP32 GPIO 13           
EN                   GND                     

Motor (4-pin connector):
Pin 1 (1A)           Motor coil 1 wire 1
Pin 2 (1B)           Motor coil 1 wire 2
Pin 3 (2A)           Motor coil 2 wire 1
Pin 4 (2B)           Motor coil 2 wire 2

================================================================================================================
TMC2209 #4 — NEMA 17 (Motor 4)
================================================================================================================
TMC2209 Pin          Connect To              
-----------          ----------              
VM                   PSU +                   100µF cap across VM and GND, close to chip
GND                  Common GND              
VIO                  ESP32 3.3V              
GND                  Common GND              
STEP                 ESP32 GPIO 33           
DIR                  ESP32 GPIO 32           
EN                   GND                     

Motor (4-pin connector):
Pin 1 (1A)           Motor coil 1 wire 1
Pin 2 (1B)           Motor coil 1 wire 2
Pin 3 (2A)           Motor coil 2 wire 1
Pin 4 (2B)           Motor coil 2 wire 2

================================================================================================================
MOTOR 5 — NEMA 17 (Z Axis) — TB6600 EXTERNAL DRIVER  *** TEMPORARY ***
================================================================================================================
Replace with TMC2209 when replacement parts arrive.

TB6600 Signal Pins:
TB6600 Pin           Connect To              Notes
----------           ----------              -----
PUL+ (STEP+)         ESP32 GPIO 4            3.3V logic OK for most TB6600s — verify yours
PUL- (STEP-)         Common GND              
DIR+                 ESP32 GPIO 5            
DIR-                 Common GND              
ENA+                 Leave floating          Driver is always enabled (mirrors TMC2209 EN=GND behavior)
ENA-                 Leave floating          

TB6600 Power Pins:
VCC                  PSU +                   Same rail as TMC2209s — TB6600 accepts 9–42V
GND                  Common GND              

TB6600 Motor Terminals:
A+                   Motor coil 1 wire 1
A-                   Motor coil 1 wire 2
B+                   Motor coil 2 wire 1
B-                   Motor coil 2 wire 2

TB6600 Dip Switch Settings (for NEMA 17, ~1.5–2A motor):
SW1 SW2 SW3          Current setting — set to 1.0A or 1.5A, check your motor's rated current
SW4 SW5 SW6          Microstepping — recommend 16 microsteps; update steps/mm in firmware to match

⚠ LOGIC LEVEL WARNING: Most TB6600s are 5V tolerant on signal pins but work fine at 3.3V.
  If your TB6600 doesn't respond to GPIO 4/5, add a 1k resistor in series on PUL+ and DIR+.

================================================================================================================
GPIO SUMMARY
================================================================================================================
GPIO         Function        Driver
----         --------        ------
4            STEP            TB6600 (Motor 5 / Z axis) *** TEMPORARY ***
5            DIR             TB6600 (Motor 5 / Z axis) *** TEMPORARY ***
13           DIR             TMC2209 #3
14           STEP            TMC2209 #3
18           STEP            TMC2209 #2
19           DIR             TMC2209 #2
26           DIR             TMC2209 #1
27           STEP            TMC2209 #1
32           DIR             TMC2209 #4
33           STEP            TMC2209 #4
EN (all)     GND             Always enabled (TMC2209s); TB6600 ENA floating = always enabled

Free safe pins available for laser PWM or other use: 21, 22, 23, 25

================================================================================================================
4-PIN CONNECTOR PINOUT
================================================================================================================
Pin 1        Coil 1A
Pin 2        Coil 1B
Pin 3        Coil 2A
Pin 4        Coil 2B

Use JST-XH 2.54mm or Dupont 4-pin — pick one and stick to it.

================================================================================================================
TODO
================================================================================================================
[ ] Print 3x additional TMC2209 mounts (in progress)
[ ] Wire all 5 motors with 4-pin connectors before MPCNC parts arrive
[ ] Install 1000µF cap at PSU output terminals
[ ] Install 1000µF cap at buck converter output before ESP32
[ ] Verify buck converter output at 5V with multimeter BEFORE connecting ESP32
[ ] Test each motor individually before full assembly
[ ] Add cable length via 4-pin extensions once mounting positions are known
[ ] Assign laser PWM to one of the free pins: 21, 22, 23, or 25
[ ] *** ORDER replacement TMC2209s — currently running TB6600 on Motor 5 (Z axis) ***
[ ] *** Swap TB6600 for TMC2209 on Motor 5 when parts arrive ***