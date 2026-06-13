# NeuroResonator — PCB Design Specification

**Board Name:** NeuroResonator v1.0  
**Board Dimensions:** 80 mm × 50 mm × 1.6 mm  
**Layer Count:** 4  
**Technology:** Mixed-signal (analog + digital + stimulation)  
**Design Tool:** KiCad 8.x  
**Revision:** 1.0  
**Date:** 2026-06-13

---

## 1. PCB Stackup (4-Layer)

### 1.1 Layer Definition

| Layer | Name | Type | Copper Weight | Function |
|-------|------|------|---------------|----------|
| L1 (Top) | Signal + Components | Signal | 1 oz (35 μm) | Analog + sensitive digital traces, all ICs, passives, connectors |
| L2 (Mid 1) | Ground Plane | Plane | 0.5 oz (18 μm) | Split: AGND / DGND / STIM_GND — solid pour with single-point bridge |
| L3 (Mid 2) | Power Plane | Plane | 0.5 oz (18 μm) | Split: 3.3V_A / 3.3V_D / 5V_STIM / VBAT |
| L4 (Bottom) | Signal + Components | Signal | 1 oz (35 μm) | Low-speed digital traces, bypass caps, pull-up/pull-down resistors |

### 1.2 Dielectric Build-Up

```
╔══════════════════════════════════════════════════════════╗
║  L1 (Top) — 1 oz Cu — Signal + Components               ║
╠══════════════════════════════════════════════════════════╣
║  PP 1080 (50% RC, 0.071mm) — εr ≈ 4.3                  ║
╠══════════════════════════════════════════════════════════╣
║  L2 (Mid 1) — 0.5 oz Cu — Ground Plane (split)          ║
╠══════════════════════════════════════════════════════════╣
║  Core 0.710mm (28 mil) — FR-4 — εr ≈ 4.5               ║
╠══════════════════════════════════════════════════════════╣
║  L3 (Mid 2) — 0.5 oz Cu — Power Plane (split)           ║
╠══════════════════════════════════════════════════════════╣
║  PP 1080 (50% RC, 0.071mm) — εr ≈ 4.3                  ║
╠══════════════════════════════════════════════════════════╣
║  L4 (Bottom) — 1 oz Cu — Signal + Components             ║
╚══════════════════════════════════════════════════════════╝

Total thickness ≈ 1.6 mm (allow ±10% tolerance)
```

### 1.3 Impedance Control

| Impedance | Target | Tolerance | Layer | Trace Geometry |
|-----------|--------|-----------|-------|----------------|
| Z₀ (single-ended) | 50 Ω | ±10% | L1 (with L2 ref) | 0.35 mm (14 mil) trace, coplanar GND on L1 with 0.2 mm gap |
| Z₀ (single-ended) | 50 Ω | ±10% | L4 (with L3 ref) | 0.35 mm (14 mil) trace, coplanar GND on L4 with 0.2 mm gap |
| USB D+/D- (if used) | 90 Ω differential | ±15% | L1 | Not differential — USB 2.0 only, no DP/DM routing required |

**Stackup notes:**
- Provide stackup detail to PCB fabricator. Specify IPC-4101/21 FR-4 (Tg ≥ 150°C, Td ≥ 325°C).
- Request prepreg/construction that yields ~0.2 mm between L1–L2 for 50 Ω traces.
- All outer-layer 50 Ω traces shall use grounded coplanar waveguide (GCPW) with via fence at ≤ λ/20 spacing.

---

## 2. Component Placement Zones

### 2.1 Zone Map

```
┌─────────────────────────────────────────────────────────────────────┐
│  0 mm                     40 mm                        80 mm        │
├────────────────────────────┬────────────────────────────────────────┤
│                            │                        ┌──────────────┤
│  ZONE A                    │  ZONE B                 │  ZONE E      │
│  Analog Front-End          │  Sensor MCU              │  Power Mgmt  │
│  ┌──────────────────┐      │  ┌─────────────┐        │  ┌────────┐  │
│  │ ADS1299          │      │  │ nRF5340     │        │  │BQ25120 │  │
│  │ Dry electrode    │      │  │ 32 MHz Xtal │        │  │LiPo    │  │
│  │ connectors (x6)  │      │  │ 32.768 kHz  │        │  │USB-C   │  │
│  │ Anti-alias       │      │  │ SPI Flash   │        │  │Voltage │  │
│  │ filters + ESD    │      │  │ BLE antenna │        │  │dividers│  │
│  └──────────────────┘      │  └─────────────┘        │  └────────┘  │
│                            │                        └──────────────┤
├────────────────────────────┼────────────────────────────────────────┤
│  ZONE C                    │  ZONE D                                 │
│  Stimulation               │  AI MCU                                 │
│  ┌──────────────────┐      │  ┌────────────────────────┐            │
│  │ DAC8562          │      │  │ ESP32-S3-WROOM-1       │            │
│  │ OPA2189          │      │  │ WiFi antenna (PCB)     │            │
│  │ ISO7740          │      │  │ USB-C data lines       │            │
│  │ Howland resistors│      │  │ PSRAM                  │            │
│  │ Stim output con. │      │  └────────────────────────┘            │
│  └──────────────────┘      │                                        │
├────────────────────────────┴────────────────────────────────────────┤
│  ZONE F (Expansion — along edges)                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ 2nd ADS  │ │ tFUS     │ │ AI Accel │ │ Ext Elec │ │ Prog Hdr │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Zone Definitions

#### Zone A: Analog Front-End  
**Location:** Top-left quadrant of board (0–35 mm X, 0–30 mm Y)  
**Isolation:** Surrounded by a continuous ground moat (AGND) on L2. No digital traces enter this zone except SPI lines (which enter via a narrow bridge under the ADS1299).  

| Component | Rationale |
|-----------|-----------|
| ADS1299 (U1) | Center of zone, 3 mm clearance from moat edge on all sides. TQFP-64 orientation: pin 1 toward top edge. |
| Dry electrode connectors (J1–J6) | Left edge of board, 7.5 mm pitch, aligned to allow shielded cable exit to the left. |
| Anti-aliasing filters | Place R/C pairs within 5 mm of each ADS1299 input pin. Use 0603 passives. |
| ESD protection diodes | Place within 3 mm of each electrode connector pin. Use ultra-low-capacitance (<0.5 pF) devices. |
| REFP/REFN filter caps | Place within 2 mm of ADS1299 pins 25/26. Use NP0/C0G dielectric. |
| AVDD + DVDD decoupling | Place within 3 mm of each respective power pin. 100 nF (0402) + 10 μF (0603) per pair. |

**Prohibited:** Any digital trace, switching node, or high-current trace.

---

#### Zone B: Sensor MCU  
**Location:** Top-center (35–55 mm X, 0–25 mm Y)  
**Ground reference:** DGND pour on L2  

| Component | Rationale |
|-----------|-----------|
| nRF5340 (U2) | Center of zone, QFN-94 with thermal pad soldered to DGND pour with 9 (3×3) thermal vias. |
| 32 MHz main crystal (X1) | Within 10 mm of XL1/XL2 pins (pins G2/G1). Load caps (C1, C2) within 1 mm of crystal pads. No digital traces under crystal area. |
| 32.768 kHz RTC crystal (X2) | Within 10 mm of XL3/XL4 pins (pins G4/G3). Load caps adjacent. |
| SPI flash (U3) | Within 15 mm of nRF5340, SPI lines routed directly. |
| BLE antenna | Edge-launch at top-right of zone (55 mm X, 0–5 mm Y). Trace runs from nRF5340 pin B6 (ANT) through balun to 50 Ω GCPW trace. |

**Keep-out zone:** BLE antenna area (55–80 mm X, 0–8 mm Y) — no copper on L1 or L4, no components.

---

#### Zone C: Stimulation  
**Location:** Bottom-left (0–35 mm X, 30–50 mm Y)  
**Isolation:** Separate ground pour (STIM_GND) on L2, isolated from DGND and AGND by a 2 mm gap. Digital isolation provided by ISO7740 — optocoupler gap on L2 (physical slot under isolator if required).  

| Component | Rationale |
|-----------|-----------|
| ISO7740 (U4) | Placed at boundary between DGND and STIM_GND zones. The isolation barrier must straddle the gap. Digital side (pins 1–7) on DGND; stim side (pins 10–16) on STIM_GND. |
| DAC8562 (U5) | On STIM_GND side, within 10 mm of ISO7740. TSSOP-16. |
| OPA2189 (U6) | On STIM_GND side, within 5 mm of DAC8562. MSOP-8. |
| Howland current-pump resistors | Precision (±0.1%) thin-film resistors. Place within 3 mm of OPA2189. Match resistor values exactly (see Section 3.5). |
| Stim output connector (J7) | Bottom edge of board, right side of zone C. |

**Spacing:** Stimulation output traces must maintain ≥2 mm clearance from all EEG input traces (Zone A).

---

#### Zone D: AI MCU  
**Location:** Right side (55–80 mm X, 8–50 mm Y)  
**Ground reference:** DGND pour on L2  

| Component | Rationale |
|-----------|-----------|
| ESP32-S3-WROOM-1 module (U7) | Edge of board. Place such that castellated edges face outward for edge-launch antenna. |
| U.FL connector (J8) | At top-right corner if using external WiFi antenna; else PCB trace antenna on the top-right area. |
| USB-C connector (J9) | Right edge, centered vertically. Data lines routed to ESP32-S3. |

---

#### Zone E: Power Management  
**Location:** Top-right (55–80 mm X, 0–25 mm Y), above Zone D  

| Component | Rationale |
|-----------|-----------|
| BQ25120 (U8) | Center of zone. DSBGA-20 — requires careful soldering. Place near LiPo connector. |
| LiPo battery connector (J10) | Top edge of board, right side. 2-pin, 2 mm pitch. |
| Voltage dividers (R1, R2, R3) | For battery monitoring (BATMON) and I²C address, placed within 5 mm of BQ25120. |
| USB-C connector (J9) | Right edge — shared with Zone D. USB VBUS routed to BQ25120 input. |
| Decoupling capacitors | Bulky caps (10–47 μF) for power rails. Low-ESR MLCC, X5R or X7R. |

---

#### Zone F: Expansion Headers  
**Location:** Distributed along board edges  

| Header | Location | Pin Count | Net Connections |
|--------|----------|-----------|-----------------|
| J11 — 2nd ADS1299 | Bottom edge, 40–50 mm X | 14 (2×7, 2.54 mm) | Redundant SPI (CS2, SCLK, DIN, DOUT, DRDY2), power (3.3V_A, GND), PWDN, RST |
| J12 — tFUS connector | Bottom edge, 10–20 mm X | 6 (2×3, 2.54 mm) | SPI, interrupt, 5V_STIM, GND |
| J13 — AI Accelerator | Right edge, below ESP32 | 10 (2×5, 2.54 mm) | I²C or SPI, interrupt, 3.3V_D, GND |
| J14 — External electrode | Left edge, below Z-A | 8 (1×8, 2.00 mm) | Direct to ADS1299 input mux, guarded traces |
| J15 — Programming/debug | Bottom edge, 60–75 mm X | 10 (2×5, 2.54 mm) | SWD (nRF5340), UART (ESP32), GND, 3.3V |

---

## 3. Detailed Netlist

### 3.1 ADS1299 (U1) — TQFP-64

**Package:** TQFP-64, 10×10 mm, 0.5 mm pitch  
**Power sequencing:** AVDD must ramp before or simultaneously with DVDD. Minimum delay from power-good to SPI access: 2 ms.

| Pin | Name | Net | Notes |
|-----|------|-----|-------|
| 1 | AVDD | +3.3V_A | Analog supply. Ferrite bead (FB1, 600 Ω @ 100 MHz) from 3.3V_A rail. Decouple: 100 nF (C101) + 10 μF (C102) to AGND. |
| 2 | AVSS | AGND | Connect directly to AGND pour. Use multiple vias to L2 AGND. |
| 3 | REFP | REFP_ADS | Positive reference input. 100 nF (C103) + 10 μF (C104) to AGND. Connect to +2.5V internal or external reference. |
| 4 | REFN | REFN_ADS | Negative reference input. Connect to AGND if using unipolar reference. 100 nF (C105) to AGND. |
| 5 | CH1+ | CH1P | Electrode channel 1, positive input. ESD diode → AGND. Series 100 Ω (R101) + 1 nF (C106) to AGND (anti-alias). Guard trace on L1 encircling input pair. |
| 6 | CH1− | CH1N | Electrode channel 1, negative input. Same protection as CH1+. |
| 7 | CH2+ | CH2P | Electrode channel 2, positive input. Same as CH1+. |
| 8 | CH2− | CH2N | Electrode channel 2, negative input. Same as CH1+. |
| 9 | CH3+ | CH3P | Electrode channel 3, positive input. Same as CH1+. |
| 10 | CH3− | CH3N | Electrode channel 3, negative input. Same as CH1+. |
| 11 | CH4+ | CH4P | Electrode channel 4, positive input. Same as CH1+. |
| 12 | CH4− | CH4N | Electrode channel 4, negative input. Same as CH1+. |
| 13 | CH5+ | CH5P | Electrode channel 5, positive input. Same as CH1+. |
| 14 | CH5− | CH5N | Electrode channel 5, negative input. Same as CH1+. |
| 15 | CH6+ | CH6P | Electrode channel 6, positive input. Same as CH1+. |
| 16 | CH6− | CH6N | Electrode channel 6, negative input. Same as CH1+. |
| 17 | CH7+ | CH7P | Electrode channel 7, positive input. Same as CH1+. |
| 18 | CH7− | CH7N | Electrode channel 7, negative input. Same as CH1+. |
| 19 | CH8+ | CH8P | Electrode channel 8, positive input. Same as CH1+. |
| 20 | CH8− | CH8N | Electrode channel 8, negative input. Same as CH1+. |
| 21 | BIAS_ELEC | BIAS_ELEC | Bias electrode output (right leg drive). 100 nF to AGND. |
| 22 | BIAS_REF | BIAS_REF | Bias reference. 1 μF (C107) to AGND. |
| 23 | BIAS_DRV | BIAS_DRV | Bias driver output. Series 10 kΩ (R102) to electrode. |
| 24 | BIAS_SENSE | BIAS_SENSE | Bias sense input. 1 μF (C108) to AGND. |
| 25 | REFN_FILT | REFN_FILT | Internal reference negative filter. 1 μF (C109) to AVSS. |
| 26 | REFP_FILT | REFP_FILT | Internal reference positive filter. 10 μF (C110) to AVSS. |
| 27 | VCAP1 | VCAP1 | Voltage regulator capacitor 1. 10 μF (C111) to AVSS. |
| 28 | VCAP2 | VCAP2 | Voltage regulator capacitor 2. 1 μF (C112) to AVSS. |
| 29 | VCAP3 | VCAP3 | Voltage regulator capacitor 3. 1 μF (C113) to AVSS. |
| 30 | VCAP4 | VCAP4 | Voltage regulator capacitor 4. 100 nF (C114) to AVSS. |
| 31 | DVDD | +3.3V_D_DVDD | Digital supply. Ferrite bead (FB2, 600 Ω @ 100 MHz) from +3.3V_D rail. Decouple: 100 nF (C115) + 10 μF (C116) to DGND. |
| 32 | DGND | DGND | Digital ground. Connect to DGND pour via dedicated via. |
| 33 | CS | NRF_SPI_CS_ADS | SPI chip select (active low). From nRF5340 P0.02 (GPIO). 10 kΩ pull-up (R103) to 3.3V_D. |
| 34 | SCLK | NRF_SPI_SCLK | SPI clock. From nRF5340 P0.11 (SPIM SCK). Series 22 Ω (R104) near source. |
| 35 | DIN | NRF_SPI_MOSI | SPI master-out-slave-in. From nRF5340 P0.12 (SPIM MOSI). Series 22 Ω (R105) near source. |
| 36 | DOUT | NRF_SPI_MISO | SPI slave-out-master-in. To nRF5340 P0.13 (SPIM MISO). Series 22 Ω (R106) near ADS1299. |
| 37 | DRDY | NRF_DRDY | Data ready (active low). To nRF5340 P0.25 (GPIOTE). 10 kΩ pull-up (R107) to 3.3V_D. |
| 38 | CLK_SEL | CLK_SEL_ADS | Clock select. Tie to DGND for internal 2.048 MHz oscillator. |
| 39 | CLK | CLK_ADS | Clock output (if CLK_SEL=0). Leave NC or use as test point. |
| 40 | PWDN | NRF_PWDN_ADS | Power-down (active low). From nRF5340 P0.26. 10 kΩ pull-up (R108) to 3.3V_D. |
| 41 | RST | NRF_RST_ADS | Reset (active low). From nRF5340 P0.27. 10 kΩ pull-up (R109) to 3.3V_D. |
| 42 | MUX_CH | NC | Not connected on base board. Leave floating or route to expansion header. |
| 43 | MUX_OUT | NC | Not connected. Leave floating. |
| 44 | BIASIN | NC | Not connected. Leave floating. |
| 45 | BIASFL | NC | Not connected. Leave floating. |
| 46 | TEST1 | NC | Connect to AGND via 0 Ω resistor if noise issues. |
| 47 | TEST2 | NC | Connect to AGND via 0 Ω resistor. |
| 48 | AVDD_IO | +3.3V_A | I/O analog supply. Decouple: 100 nF (C117) + 10 μF (C118) to AGND. |
| 49 | AVSS_IO | AGND | Connect to AGND pour. |
| 50 | AVDD1 | +3.3V_A | Analog supply 1. Decouple: 100 nF (C119) + 10 μF (C120) to AGND. |
| 51 | AVSS1 | AGND | Connect to AGND pour. |
| 52 | AVDD2 | +3.3V_A | Analog supply 2. Decouple: 100 nF (C121) + 10 μF (C122) to AGND. |
| 53 | AVSS2 | AGND | Connect to AGND pour. |
| 54 | AVDD3 | +3.3V_A | Analog supply 3. Decouple: 100 nF (C123) + 10 μF (C124) to AGND. |
| 55 | AVSS3 | AGND | Connect to AGND pour. |
| 56 | AVDD4 | +3.3V_A | Analog supply 4. Decouple: 100 nF (C125) + 10 μF (C126) to AGND. |
| 57 | AVSS4 | AGND | Connect to AGND pour. |
| 58 | AVDD5 | +3.3V_A | Analog supply 5. Decouple: 100 nF (C127) + 10 μF (C128) to AGND. |
| 59 | AVSS5 | AGND | Connect to AGND pour. |
| 60 | AVDD6 | +3.3V_A | Analog supply 6. Decouple: 100 nF (C129) + 10 μF (C130) to AGND. |
| 61 | AVSS6 | AGND | Connect to AGND pour. |
| 62 | AVDD7 | +3.3V_A | Analog supply 7. Decouple: 100 nF (C131) + 10 μF (C132) to AGND. |
| 63 | AVSS7 | AGND | Connect to AGND pour. |
| 64 | AVDD8 | +3.3V_A | Analog supply 8. Decouple: 100 nF (C133) + 10 μF (C134) to AGND. |

**Thermal pad:** Exposed pad on bottom of TQFP-64. Solder to AGND pour with 4 (2×2) thermal vias. Do not solder to L2 AGND plane — only to top-layer pad with vias connecting to L2.

---

### 3.2 nRF5340 (U2) — QFN-94 (aQFN)

**Package:** QFN-94, 7×7 mm, 0.4 mm pitch  
**Power domains:** VDD (1.7–3.6 V core), VDD_IO (1.7–3.6 V I/O) — both connected to 3.3V_D.  
**Decoupling:** Each VDD/VDD_IO pin pair requires 100 nF (0402) + 10 μF (0603). See detailed map below.

| Pin | Name | Net | Notes |
|-----|------|-----|-------|
| A1 | P0.13 | NRF_SPI_MISO | SPIM MISO → ADS1299 DOUT, DAC8562 SDO |
| A2 | P0.12 | NRF_SPI_MOSI | SPIM MOSI → ADS1299 DIN, DAC8562 SDIN |
| A3 | P0.11 | NRF_SPI_SCLK | SPIM SCK → ADS1299 SCLK, DAC8562 SCLK |
| A4 | P0.10 | NC | Leave floating or GPIO expansion |
| A5 | P0.09 | NRF_UART_TX | UART TX → ESP32-S3 RX |
| A6 | P0.08 | NRF_UART_RX | UART RX → ESP32-S3 TX |
| A7 | P0.07 | NRF_SPI_CS_DAC | SPIM CS → DAC8562 SYNC pin |
| A8 | P0.06 | NC | Leave floating or GPIO |
| A9 | P0.05 | NRF_SPI_CS_FLASH | SPIM CS → SPI flash CS |
| A10 | P0.04 | NRF_SPI_MISO_F | SPIM MISO → SPI flash DO |
| A11 | P0.03 | NRF_SPI_MOSI_F | SPIM MOSI → SPI flash DI |
| A12 | P0.02 | NRF_SPI_CS_ADS | SPIM CS → ADS1299 CS |
| A13 | P0.01 | NRF_I2C_SCL | I2C SCL → BQ25120 SCL (pull-up 4.7 kΩ to 3.3V_D) |
| A14 | P0.00 | NRF_I2C_SDA | I2C SDA → BQ25120 SDA (pull-up 4.7 kΩ to 3.3V_D) |

| B1 | P0.31 | NC | Leave floating |
| B2 | P0.30 | NC | Leave floating |
| B3 | P0.29 | NC | Leave floating |
| B4 | P0.28 | NC | Leave floating |
| B5 | P0.27 | NRF_RST_ADS | GPIO → ADS1299 RST |
| B6 | ANT | BLE_ANT | BLE antenna output. Route through balun (L1, C1, C2 from nRF5340 REF schematic) to 50 Ω trace. |
| B7 | VDD_IO1 | +3.3V_D | I/O supply 1. Decouple: 100 nF (C201) + 10 μF (C202) to DGND. |
| B8 | VDD_IO2 | +3.3V_D | I/O supply 2. Decouple: 100 nF (C203) + 10 μF (C204) to DGND. |
| B9 | VDD_IO3 | +3.3V_D | I/O supply 3. Decouple: 100 nF (C205) + 10 μF (C206) to DGND. |
| B10 | VDD_IO4 | +3.3V_D | I/O supply 4. Decouple: 100 nF (C207) + 10 μF (C208) to DGND. |
| B11 | VDD_IO5 | +3.3V_D | I/O supply 5. Decouple: 100 nF (C209) + 10 μF (C210) to DGND. |
| B12 | VDD_IO6 | +3.3V_D | I/O supply 6. Decouple: 100 nF (C211) + 10 μF (C212) to DGND. |
| B13 | VDD_IO7 | +3.3V_D | I/O supply 7. Decouple: 100 nF (C213) + 10 μF (C214) to DGND. |
| B14 | P0.26 | NRF_PWDN_ADS | GPIO → ADS1299 PWDN |

| C1 | P0.25 | NRF_DRDY | GPIO/GPIOTE → ADS1299 DRDY |
| C2 | P0.24 | NC | Leave floating |
| C3 | P0.23 | NRF_ISO_IN1 | GPIO → ISO7740 IN1 |
| C4 | P0.22 | NRF_ISO_IN2 | GPIO → ISO7740 IN2 |
| C5 | P0.21 | NC | Leave floating |
| C6 | P0.20 | NRF_ISO_IN3 | GPIO → ISO7740 IN3 |
| C7 | P0.19 | NRF_ISO_IN4 | GPIO → ISO7740 IN4 |
| C8 | P0.18 | NC | Leave floating |
| C9 | P0.17 | NRF_FLASH_HOLD | GPIO → SPI flash HOLD (or NC) |
| C10 | P0.16 | NRF_FLASH_WP | GPIO → SPI flash WP (or NC) |
| C11 | P0.15 | NC | Leave floating |
| C12 | P0.14 | NC | Leave floating |
| C13 | SWDIO | SWDIO | SWD data I/O. 10 kΩ pull-up (R210) to 3.3V_D. Route to programming header J15. |
| C14 | SWCLK | SWCLK | SWD clock. 10 kΩ pull-down (R211) to DGND. Route to J15. |

| D1 | RESET | NRF_RESET | Active-low reset. 10 kΩ pull-up (R212) to 3.3V_D. Decouple 100 nF (C215) to DGND. |
| D2 | TRACECLK | NC | Leave floating |
| D3 | TRACEDATA0 | NC | Leave floating |
| D4 | TRACEDATA1 | NC | Leave floating |
| D5 | TRACEDATA2 | NC | Leave floating |
| D6 | TRACEDATA3 | NC | Leave floating |
| D7 | VREG_IN | VREG_IN | Internal regulator input. Connect to VDD_IO. Decouple: 100 nF (C216) + 10 μF (C217) to DGND. |
| D8 | VREG_OUT | VREG_OUT | Internal regulator output. Decouple: 100 nF (C218) + 10 μF (C219) to DGND. |
| D9 | VDD | +3.3V_D | Core supply. Decouple: 100 nF (C220) + 10 μF (C221) to DGND. |
| D10 | VDD | +3.3V_D | Core supply. Decouple: 100 nF (C222) + 10 μF (C223) to DGND. |
| D11 | VDD_IO8 | +3.3V_D | I/O supply 8. Decouple: 100 nF (C224) + 10 μF (C225) to DGND. |
| D12 | VDD_IO9 | +3.3V_D | I/O supply 9. Decouple: 100 nF (C226) + 10 μF (C227) to DGND. |
| D13 | VDD_IO10 | +3.3V_D | I/O supply 10. Decouple: 100 nF (C228) + 10 μF (C229) to DGND. |
| D14 | VDD_IO11 | +3.3V_D | I/O supply 11. Decouple: 100 nF (C230) + 10 μF (C231) to DGND. |

| E1 | D- | NC | USB D− (not used on nRF5340 in this design). Leave floating. |
| E2 | D+ | NC | USB D+ (not used on nRF5340 in this design). Leave floating. |
| E3 | VBUS | NC | USB VBUS sense (not used). Leave floating. |
| E4–E7 | NC | — | No internal connection. Leave floating. |
| E8 | XL1 | X1_IN | 32 MHz crystal input. Load cap C232 to DGND. 1 MΩ feedback resistor internal. |
| E9 | XL2 | X1_OUT | 32 MHz crystal output. Load cap C233 to DGND. |
| E10 | XL3 | X2_IN | 32.768 kHz crystal input. Load cap C234 to DGND. |
| E11 | XL4 | X2_OUT | 32.768 kHz crystal output. Load cap C235 to DGND. |
| E12 | VDD_IO12 | +3.3V_D | I/O supply 12. Decouple: 100 nF (C236) + 10 μF (C237) to DGND. |
| E13 | VDD_IO13 | +3.3V_D | I/O supply 13. Decouple: 100 nF (C238) + 10 μF (C239) to DGND. |
| E14 | VDD_IO14 | +3.3V_D | I/O supply 14. Decouple: 100 nF (C240) + 10 μF (C241) to DGND. |

| F1–F6 | NC | — | No internal connection. Leave floating or connect to DGND. |
| F7 | VDD | +3.3V_D | Core supply. Decouple: 100 nF (C242) + 10 μF (C243) to DGND. |
| F8–F14 | NC | — | No internal connection. Leave floating. |

| G1 | XL2 | X1_OUT | Same as E9 (connected internally — route to crystal). |
| G2 | XL1 | X1_IN | Same as E8 (connected internally — route to crystal). |
| G3 | XL4 | X2_OUT | Same as E11 (connected internally — route to crystal). |
| G4 | XL3 | X2_IN | Same as E10 (connected internally — route to crystal). |
| G5–G7 | VSS | DGND | Ground pins. Connect directly to DGND pour with via-in-pad or adjacent via per pin. |
| G8–G14 | VSS | DGND | Ground pins. Same as G5–G7. |

| H1–H14 | VSS | DGND | Ground pins. All must connect to DGND pour with low impedance. |

**Crystal specifications:**
- **32 MHz:** Load capacitance CL = 12 pF, ESR ≤ 40 Ω. Use NX3225SA or similar. Load caps: C232 = C233 = 18 pF (assuming 6 pF parasitic).
- **32.768 kHz:** Load capacitance CL = 12.5 pF, ESR ≤ 70 kΩ. Use FC-135 or similar. Load caps: C234 = C235 = 22 pF (assuming 6 pF parasitic).

**Thermal pad:** Large central pad. Solder to DGND pour with 5×5 array of 0.3 mm thermal vias. Flood bottom side of vias to L2 DGND.

---

### 3.3 DAC8562 (U5) — TSSOP-16

**Package:** TSSOP-16, 5×4.4 mm, 0.65 mm pitch  
**Reference:** Use internal 2.5 V reference (REF_EN = 1) or external REFP.

| Pin | Name | Net | Notes |
|-----|------|-----|-------|
| 1 | SYNC | NRF_SPI_CS_DAC | SPI chip select (active low). From nRF5340 P0.07. 10 kΩ pull-up (R501) to 3.3V_STIM. |
| 2 | SCLK | NRF_SPI_SCLK | SPI clock. From nRF5340 P0.11. Series 22 Ω (R502) near source. |
| 3 | SDIN | NRF_SPI_MOSI | SPI data in. From nRF5340 P0.12. Series 22 Ω (R503) near source. |
| 4 | SDO | NRF_ISO_OUT1 | SPI data out. To ISO7740 IN1 (isolated back to nRF5340). |
| 5 | LDAC | STIM_LDAC | Load DAC (active low). Tie to DGND (transparent mode) or GPIO. 10 kΩ pull-up (R504) to 3.3V_STIM if GPIO-controlled. |
| 6 | CLR | STIM_CLR | Clear (active low). Tie to 3.3V_STIM via 10 kΩ (R505) if not used. |
| 7 | REF_EN | DAC_REF_EN | Internal reference enable. Tie to 3.3V_STIM via 10 kΩ (R506) to enable 2.5 V internal reference. |
| 8 | REF_WF | REF_WF_DAC | Reference write filter. 100 nF (C501) to STIM_GND. |
| 9 | REFP | DAC_REFP | Positive reference. 10 μF (C502) + 100 nF (C503) to STIM_GND. If internal reference used, output is 2.5 V. |
| 10 | REFN | STIM_GND | Negative reference. Connect to STIM_GND. |
| 11 | VOUTA | DAC_OUTA | DAC channel A output. Connect to Howland current-pump input (R601 on OPA2189 Amp A). |
| 12 | VOUTB | DAC_OUTB | DAC channel B output. Connect to Howland current-pump input (R602 on OPA2189 Amp B). |
| 13 | AVDD | +5V_STIM | Analog supply. Decouple: 100 nF (C504) + 10 μF (C505) to STIM_GND. |
| 14 | DVDD | +5V_STIM | Digital supply. 100 Ω ferrite bead (FB501) from +5V_STIM. Decouple: 100 nF (C506) + 10 μF (C507) to STIM_GND. |
| 15 | GND | STIM_GND | Ground. Connect to STIM_GND pour. |
| 16 | IOVDD | +5V_STIM | I/O supply. Decouple: 100 nF (C508) + 10 μF (C509) to STIM_GND. |

---

### 3.4 OPA2189 (U6) — MSOP-8

**Package:** MSOP-8, 3×3 mm, 0.65 mm pitch  

| Pin | Name | Net | Notes |
|-----|------|-----|-------|
| 1 | OUTA | STIM_OUTA | Output of amplifier A → Howland current-pump output channel A. |
| 2 | −INA | HOW_A_NEG | Inverting input A. Connects to precision resistor feedback network. |
| 3 | +INA | HOW_A_POS | Non-inverting input A. Connects to voltage divider from DAC_OUTA. |
| 4 | V− | STIM_GND | Negative supply (GND). Connect to STIM_GND pour. |
| 5 | +INB | HOW_B_POS | Non-inverting input B. Connects to voltage divider from DAC_OUTB. |
| 6 | −INB | HOW_B_NEG | Inverting input B. Connects to precision resistor feedback network. |
| 7 | OUTB | STIM_OUTB | Output of amplifier B → Howland current-pump output channel B. |
| 8 | V+ | +5V_STIM | Positive supply. Decouple: 100 nF (C601) + 10 μF (C602) to STIM_GND. |

**Howland Current Pump Configuration (Channel A, identical for B):**

```
                 ┌──── R603 (10 kΩ, ±0.1%) ────┐
                 │                              │
V_A_POS ── R601 ─┼──── +OPA2189                │
(10 kΩ, 0.1%)    │       │                      │
                 │       │                      │
                 └──── R604 (10 kΩ, ±0.1%) ──────┼── STIM_OUTA
                         │                      │
                        GND                    LOAD
```

- R601 = R602 = R603 = R604 = R605 = R606 = **10 kΩ, ±0.1%, 25 ppm/°C, thin-film** — all four resistors must be matched.
- Output current: Iout = (Vin × R603) / (R601 × Rload). With R601 = R603 = 10 kΩ, Iout = Vin / Rload. For Vin = 0–2.5 V, output current is 0–±2.5 mA (limited by Rload and supply).
- Limiting resistor: R_limit (R607) = 100 Ω in series with output for short-circuit protection.
- Feedback cap: C603 = 10 pF (NP0/C0G) across R603 for stability.

---

### 3.5 ISO7740 (U4) — SOIC-16

**Package:** SOIC-16 wide-body, 10.3×7.5 mm, 1.27 mm pitch  

| Pin | Name | Net | Notes |
|-----|------|-----|-------|
| 1 | VCC1 | +3.3V_D | Digital side supply. Decouple: 100 nF (C401) + 10 μF (C402) to DGND. |
| 2 | IN1 | NRF_ISO_IN1 | Channel 1 input (from nRF5340 P0.23) |
| 3 | IN2 | NRF_ISO_IN2 | Channel 2 input (from nRF5340 P0.22) |
| 4 | IN3 | NRF_ISO_IN3 | Channel 3 input (from nRF5340 P0.20) |
| 5 | IN4 | NRF_ISO_IN4 | Channel 4 input (from nRF5340 P0.19) |
| 6 | GND1 | DGND | Digital side ground. Connect to DGND pour. |
| 7 | NC | — | No connect. Leave floating. |
| 8 | EN1 | +3.3V_D | Enable (active high). Tie to VCC1 via 10 kΩ (R401). |
| 9 | NC | — | No connect. Leave floating. |
| 10 | GND2 | STIM_GND | Stim side ground. Connect to STIM_GND pour. |
| 11 | OUT4 | NRF_ISO_OUT4 | Channel 4 output → nRF5340 P0.?? (or NC) |
| 12 | OUT3 | NRF_ISO_OUT3 | Channel 3 output → nRF5340 P0.?? (or NC) |
| 13 | OUT2 | NRF_ISO_OUT2 | Channel 2 output → nRF5340 P0.?? (or NC) |
| 14 | OUT1 | NRF_ISO_OUT1 | Channel 1 output → nRF5340 P0.?? (or nRF5340 receives DAC8562 SDO) |
| 15 | VCC2 | +5V_STIM | Stim side supply. Decouple: 100 nF (C403) + 10 μF (C404) to STIM_GND. |
| 16 | NC | — | No connect. Leave floating. |

**Isolation barrier:** The isolation gap on L2 (ground plane split between DGND and STIM_GND) must be at least 2 mm wide and must pass under the center of the ISO7740 package. No traces or copper in this gap on any layer.

---

### 3.6 BQ25120 (U8) — DSBGA-20

**Package:** DSBGA-20, 2.0×1.6 mm, 0.4 mm pitch  
**Caution:** Ultra-fine pitch BGA. Requires solder paste deposition with stencil aperture ≤ 0.25 mm. X-ray inspection mandatory.

| Pin | Name | Net | Notes |
|-----|------|-----|-------|
| A1 | PMID | BQ_PMID | Power mid-point. Connect to VBAT path. 10 μF (C801) to PGND. |
| A2 | SDA | NRF_I2C_SDA | I²C data (open-drain). 4.7 kΩ pull-up (R801) to 3.3V_D. |
| A3 | SCL | NRF_I2C_SCL | I²C clock (open-drain). 4.7 kΩ pull-up (R802) to 3.3V_D. |
| A4 | GND | PGND | Power ground for charger. Connect to PGND pour (separate from DGND/AGND, connected to battery negative). |
| B1 | VBUS | USB_VBUS | USB input voltage (4.5–5.5 V). 1 μF (C802) to PGND. |
| B2 | VIN | BQ_VIN | Input voltage from VBUS. 10 μF (C803) to PGND. |
| B3 | BAT | BQ_BAT | Battery connection. Connect to LiPo (+) via 0 Ω sense resistor (R803). 10 μF (C804) to PGND. |
| B4 | GND | PGND | Power ground. |
| C1 | VOUT1 | +3.3V_D | LDO1 output (programmable 1.2–3.3 V). Set to 3.3 V via I²C. 10 μF (C805) + 100 nF (C806) to DGND. |
| C2 | VOUT2 | +5V_STIM | LDO2 output (programmable 1.2–5.0 V). Set to 5.0 V via I²C. 10 μF (C807) + 100 nF (C808) to STIM_GND. |
| C3 | EN1 | BQ_EN1 | Enable LDO1 (active high). Tie to VBAT via 100 kΩ (R804). |
| C4 | EN2 | BQ_EN2 | Enable LDO2 (active high). Tie to VBAT via 100 kΩ (R805). |
| D1 | VOUT3 | +3.3V_A | LDO3 output (programmable 1.2–3.3 V). Set to 3.3 V via I²C. 10 μF (C809) + 100 nF (C810) to AGND. |
| D2 | MR | BQ_MR | Manual reset (active low). Tie to VBAT via 100 kΩ (R806) or GPIO for watchdog reset. |
| D3 | PG | BQ_PG | Power good (open drain). 10 kΩ pull-up (R807) to 3.3V_D. |
| D4 | ILIM | BQ_ILIM | Charge current limit. Resistor-divider to PGND. Use 19.1 kΩ for 100 mA charge current. |
| E1 | BATMON | BQ_BATMON | Battery monitor. Voltage divider R808 (1 MΩ) / R809 (200 kΩ) to PGND. Ratio 1:5 → ADC input 0–1.2 V for 0–6 V battery. |
| E2 | VSET | BQ_VSET | Battery regulation voltage set. Resistor to PGND. 84.5 kΩ → 4.2 V. |
| E3 | ISET | BQ_ISET | Pre-charge current set. Resistor to PGND. 8.87 kΩ → 10% of ILIM. |
| E4 | TS | BQ_TS | Thermistor input. 10 kΩ NTC (R810) from TS to PGND; 1 kΩ (R811) from TS to BQ_VOUT / appropriate pull-up. |

**Power path:** USB VBUS → BQ25120 → Battery (while charging), and Battery → BQ25120 LDOs → System (when USB removed). The BQ25120 automatically power-paths.

---

### 3.7 ESP32-S3-WROOM-1 (U7) — Module

**Package:** ESP32-S3-WROOM-1 castellated module (56 pins at 2.0 mm pitch on 2 sides), 25.5×18.0 mm  
**Module lands:** Use castellated edge pads — no soldering of bottom pad (module has internal shielding).  

| Pin | Name | Net | Notes |
|-----|------|-----|-------|
| 1 | GND | DGND | Connect to DGND pour with via. |
| 2 | GPIO1 | NC | Leave floating or GPIO expansion |
| 3 | GPIO2 | NC | Leave floating |
| 4 | GPIO3 | NC | Leave floating |
| 5 | GPIO4 | ESP_INT_ACCEL | Interrupt to AI accelerator header J13 |
| 6 | GPIO5 | NC | Leave floating |
| 7 | GPIO6 | NC | Leave floating |
| 8 | GPIO7 | NC | Leave floating |
| 9 | GPIO8 | NC | Leave floating |
| 10 | GPIO9 | NC | Leave floating |
| 11 | GPIO10 | NC | Leave floating |
| 12 | GPIO11 | NC | Leave floating |
| 13 | GPIO12 | NC | Leave floating |
| 14 | GPIO13 | NC | Leave floating |
| 15 | GPIO14 | NC | Leave floating |
| 16 | GPIO15 | NC | Leave floating |
| 17 | GPIO16 | NC | Leave floating |
| 18 | GPIO17 | NC | Leave floating |
| 19 | GPIO18 | NC | Leave floating |
| 20 | GPIO19 | NC | Leave floating |
| 21 | GPIO20 | NC | Leave floating |
| 22 | GPIO21 | ESP_UART_RX | UART RX → nRF5340 TX |
| 23 | GPIO22 | ESP_UART_TX | UART TX → nRF5340 RX |
| 24 | GPIO23 | NC | Leave floating |
| 25 | GPIO24 | NC | Leave floating |
| 26 | GPIO25 | NC | Leave floating |
| 27 | GPIO26 | NC | Leave floating |
| 28 | GPIO27 | NC | Leave floating |
| 29 | GPIO28 | NC | Leave floating |
| 30 | GPIO29 | NC | Leave floating |
| 31 | GPIO30 | NC | Leave floating |
| 32 | GPIO31 | NC | Leave floating |
| 33 | GPIO32 | NC | Leave floating |
| 34 | GPIO33 | NC | Leave floating |
| 35 | GPIO34 | NC | Leave floating |
| 36 | GPIO35 | NC | Leave floating |
| 37 | GPIO36 | NC | Leave floating |
| 38 | GPIO37 | NC | Leave floating |
| 39 | GPIO38 | NC | Leave floating |
| 40 | GPIO39 | NC | Leave floating |
| 41 | GPIO40 | NC | Leave floating |
| 42 | GPIO41 | NC | Leave floating |
| 43 | GPIO42 | NC | Leave floating |
| 44 | GPIO43 | NC | Leave floating |
| 45 | GPIO44 | NC | Leave floating |
| 46 | GPIO45 | NC | Leave floating |
| 47 | GPIO46 | NC | Leave floating |
| 48 | GPIO47 | NC | Leave floating |
| 49 | GPIO48 | ESP_GPIO49 | Strapping pin (MTDO). 10 kΩ pull-down (R701) for download mode. |
| 50 | GPIO49 | ESP_GPIO49 | Strapping pin (GPIO49). 10 kΩ pull-up (R702) for boot mode. |
| 51 | GPIO50 | ESP_GPIO50 | Strapping pin. Tie to GND for SPI boot. |
| 52 | RST | ESP_RST | Reset (active low). 10 kΩ pull-up (R703) to 3.3V_D. 100 nF (C701) to DGND. |
| 53 | EN | ESP_EN | Enable. 10 kΩ pull-up (R704) to 3.3V_D. |
| 54 | USB_D− | USB_DN | USB D− → USB-C (skip if using UART-only communication) |
| 55 | USB_D+ | USB_DP | USB D+ → USB-C (skip if using UART-only communication) |
| 56 | VBUS | USB_VBUS | USB VBUS (5 V input). 0.1 μF (C702) to DGND. |

**Antenna:** The ESP32-S3-WROOM-1 module has an integrated PCB antenna on the module itself. Ensure the module overhangs the carrier PCB such that the antenna section is not over copper. If using a U.FL connector, connect to module ANT pad through a 0 Ω jumper and ensure 50 Ω trace.

**Strapping pins:** During power-up, GPIO48, GPIO49, GPIO50 are sampled. Set default values as shown to ensure normal SPI flash boot mode.

---

## 4. Power Tree

### 4.1 Power Architecture Diagram

```
                              ┌──────────────────────┐
                              │    USB-C VBUS        │
                              │    (5V, 3A max)      │
                              └──────────┬───────────┘
                                         │
                                    ┌────▼────┐
                                    │BQ25120  │
                                    │ Charger │
                                    │ + LDOs  │
                                    └──┬──┬───┘
                                       │  │
                        ┌──────────────┘  └──────────────┐
                        │                                 │
                   ┌────▼─────┐                     ┌─────▼────┐
                   │  LDO1    │                     │  LDO2    │
                   │ +3.3V_D  │                     │ +5V_STIM │
                   │ 3.3V     │                     │ 5.0V     │
                   │ 600mA    │                     │ 300mA    │
                   └──┬──┬────┘                     └──┬──┬─────┘
                      │  │                             │  │
         ┌────────────┘  └─────────┐     ┌────────────┘  └─────────┐
         │                         │     │                         │
    ┌────▼────┐              ┌─────▼──┐  │                  ┌──────▼───┐
    │ nRF5340 │              │ ESP32  │  │                  │ DAC8562  │
    │ 3.3V_D  │              │ -S3    │  │                  │ +5V_STIM │
    │ 10mA    │              │ 3.3V_D │  │                  │ 1.5mA    │
    └─────────┘              │ 80mA   │  │                  └──────────┘
                             └────────┘  │
                                │        │            ┌──────▼───┐
                                │        │            │ OPA2189  │
                                │        │            │ +5V_STIM │
                                │        │            │ 1.2mA    │
                                │        │            └──────────┘
                                │        │
                                │        │            ┌──────▼───┐
                                │        │            │ ISO7740  │
                                │        │            │ (stim)   │
                                │        │            │ +5V_STIM │
                                │        │            │ 5mA      │
                                │        │            └──────────┘
                           ┌────▼────┐              ┌──────▼───┐
                           │  LDO3   │              │ ISO7740  │
                           │ +3.3V_A │              │ (dig)    │
                           │ 3.3V    │              │ +3.3V_D  │
                           │ 50mA    │              │ 5mA      │
                           └──┬──┬───┘              └──────────┘
                              │  │
                 ┌────────────┘  └──────────┐
                 │                          │
            ┌────▼─────┐              ┌─────▼──────┐
            │ ADS1299  │              │ Ferrite    │
            │ AVDD     │              │ Bead FB2   │
            │ +3.3V_A  │              │ → DVDD     │
            │ 20mA     │              │ +3.3V_D    │
            └──────────┘              └────────────┘
```

**Battery path (USB disconnected):**
```
402030 LiPo (4.2V, 400 mAh) ──→ BQ25120 BAT pin ──→ LDOs (same as above)
```

### 4.2 Rail Specifications

| Rail | Source | Voltage | Tolerance | Max Current | Ripple Tol. | Decoupling Strategy |
|------|--------|---------|-----------|-------------|-------------|---------------------|
| +3.3V_D | BQ25120 LDO1 | 3.3 V | ±1.5% (3.25–3.35 V) | 600 mA | <30 mVpp | 10 μF × 4 + 100 nF × 20 distributed at each IC |
| +3.3V_A | BQ25120 LDO3 | 3.3 V | ±1.0% (3.267–3.333 V) | 50 mA | <10 mVpp | 10 μF × 2 + 100 nF × 10 at ADS1299; ferrite bead FB1 (600 Ω @ 100 MHz) between rail and ADS1299 AVDD pins |
| +5V_STIM | BQ25120 LDO2 | 5.0 V | ±2% (4.9–5.1 V) | 300 mA | <50 mVpp | 10 μF × 3 + 100 nF × 10 distributed |
| VBAT | BQ25120 BAT | 3.0–4.2 V | (battery dependent) | 400 mA max charge | — | 10 μF at BQ25120, routed directly to battery |

### 4.3 Power Plane Splitting (L3)

- **3.3V_D plane:** Covers nRF5340 (Zone B), ESP32-S3 (Zone D), and digital side of ISO7740 (Zone C boundary). Connected to BQ25120 LDO1.
- **3.3V_A plane:** Covers ADS1299 (Zone A) and analog side of any isolation crossing. Connected to BQ25120 LDO3 through ferrite bead FB1. Small pour, isolated from 3.3V_D by ≥0.5 mm gap.
- **5V_STIM plane:** Covers DAC8562, OPA2189, and stim side of ISO7740 (Zone C). Connected to BQ25120 LDO2. Isolated from other planes by ≥1.0 mm gap.
- **VBAT plane:** Small pour under BQ25120, connecting to battery connector. No other loads.

---

## 5. Routing Guidelines

### 5.1 General Parameters

| Parameter | Value | Applies To |
|-----------|-------|------------|
| Min trace width | 6 mil (0.15 mm) | Digital signals, general routing |
| Recommended trace width | 8 mil (0.20 mm) | SPI, I²C, GPIO, UART |
| Analog trace width | 10 mil (0.25 mm) | EEG inputs, reference lines |
| Power trace width (3.3V) | 30 mil (0.76 mm) | 3.3V_D, 3.3V_A rails |
| Power trace width (5V) | 50 mil (1.27 mm) | +5V_STIM, VBAT |
| Min clearance | 6 mil (0.15 mm) | General |
| Recommended clearance | 10 mil (0.25 mm) | Between analog and digital |
| Stim-to-EEG clearance | 2 mm (80 mil) | Minimum spacing between stim and EEG traces |
| Via drill diameter | 0.3 mm (12 mil) | Signal vias |
| Via pad diameter | 0.6 mm (24 mil) | Signal vias |
| Thermal via drill | 0.3 mm (12 mil) | QFN thermal pads |

### 5.2 Analog Trace Routing (EEG Inputs — Zone A)

- Route each EEG input as a differential **pair** (CHn+, CHn−) with **10 mil** width and **10 mil** spacing between the pair.
- Maintain **20 mil** clearance from all other signals (including other EEG pairs).
- **Guard ring:** Enclose each input pair with a GND copper pour (AGND) on L1. The guard ring must be at least 10 mil wide and connect to AGND with vias every 5 mm.
- Avoid 90° corners — use 45° or curved bends.
- Keep trace length from electrode connector to ADS1299 input pin under 20 mm.
- No vias on EEG input traces if possible (minimize parasitics). If a via is required, use a small via (0.25 mm drill) and verify crosstalk.

### 5.3 SPI Routing

**Affected buses:**
- **nRF5340 → ADS1299:** CS, SCLK, MOSI, MISO, DRDY
- **nRF5340 → DAC8562:** CS, SCLK, MOSI, SDO (through ISO7740)
- **nRF5340 → SPI Flash:** CS, SCLK, MOSI, MISO

**Guidelines:**
- Trace width: 8 mil (0.20 mm), clearance: 10 mil.
- Group SPI lines together in a bundle. Avoid splitting across ground plane gaps.
- **Length matching:** Match all signals in a SPI bus to within 50 mm (very relaxed for ≤2 MHz SPI). Simply keep traces roughly equal.
- **Series termination:** Place 22 Ω (R104–R106, R502–R503) within 5 mm of the source (nRF5340 side) on SCLK, MOSI, and the ADS1299 CS line. For MISO (DOUT from ADS1299), place 22 Ω at the ADS1299 end.
- Route SPI traces over a solid ground plane (no splits). If crossing the AGND/DGND moat, route through the bridge under ADS1299 only.

### 5.4 Antenna Traces (BLE — nRF5340)

**Target impedance:** 50 Ω single-ended, ±10%  
**Topology:** Coplanar waveguide with ground (CPWG) on L1, reference to L2 DGND.

- Trace width: **0.35 mm (14 mil)**
- Gap to coplanar GND: **0.2 mm (8 mil)** on each side
- GND stitching vias along both sides at **3 mm** intervals (≤ λ/20 at 2.4 GHz)
- **Route length:** Keep trace from balun output to antenna feed point under 30 mm.
- Minimize bends — use a single 45° bend if needed; avoid 90°.
- **Keep-out zone:** The antenna area (55–80 mm × 0–8 mm) must have no copper on any layer (L1, L2, L3, L4) except the antenna trace itself and the GND coplanar pour. No traces, no vias, no components in this zone.
- **Balun:** Use Johanson 2450BM14E0017 or equivalent (3.2×1.6 mm laminate). Place within 5 mm of nRF5340 ANT pin (B6).

### 5.5 Antenna Traces (WiFi — ESP32-S3)

- The ESP32-S3-WROOM-1 module has an integrated PCB antenna. The module must be placed such that the antenna portion extends beyond the carrier PCB edge (overhang). No copper under the module's antenna section.
- If using an external antenna via U.FL:
  - U.FL connector at edge of board (top-right corner).
  - 50 Ω CPWG trace (14 mil width, 8 mil gap) from module ANT pad to U.FL center pin.
  - Use a 0 Ω jumper to select internal vs. external antenna.

### 5.6 I²C Routing

- **nRF5340 → BQ25120:** SDA, SCL
- Trace width: 8 mil, clearance: 10 mil.
- Keep trace length under 50 mm.
- Pull-up resistors (R801, R802 = 4.7 kΩ) placed at the nRF5340 end.

### 5.7 UART Routing

- **nRF5340 → ESP32-S3:** TX, RX (cross-connected)
- Trace width: 8 mil, clearance: 10 mil.
- No series termination needed (3.3 V logic, short trace).

### 5.8 USB-C Routing

- The USB-C connector (J9) is used primarily for **charging** (VBUS, GND).
- Data lines (D+, D−) are optional — route if USB data communication is desired.
- **If D+/D− are used:** Route as a 90 Ω differential pair using 8 mil traces with 8 mil spacing, length match within 5 mm. Reference to GND on L2.
- **VBUS:** Route with 50 mil width (to handle 2 A charging current). Add a 10 μF capacitor at the connector.
- **CC1/CC2:** Connect to 5.1 kΩ pull-down resistors (R901, R902) to GND to indicate sink mode.
- **Shield:** Connect to chassis GND via 1 MΩ + 10 nF RC (R903, C901) to DGND for EMC.

### 5.9 Power Routing

- **3.3V_D:** Star distribution from BQ25120 LDO1 output. Use 30 mil traces. Distribute 10 μF + 100 nF at each branch point.
- **3.3V_A:** Route from BQ25120 LDO3 through ferrite bead FB1 (600 Ω @ 100 MHz, rated 100 mA) before reaching ADS1299. 20 mil trace.
- **5V_STIM:** Route from BQ25120 LDO2 to Zone C with 50 mil traces. No ferrite (stimulation needs clean but higher current).
- **VBAT:** Route from battery connector to BQ25120 with 50 mil traces. Fuse (F1, 1 A resettable PTC) in series with battery positive.

### 5.10 Ground Plane Design (L2)

- **AGND pour:** Covers Zone A entirely. Moated — no copper connection to DGND except at the single bridge point.
- **DGND pour:** Covers Zone B, D, E, and F.
- **STIM_GND pour:** Covers Zone C entirely. Isolated from DGND by ≥2 mm gap.
- **Single-point bridge:** The AGND ↔ DGND bridge is a 5 mm wide copper strap located directly under the ADS1299 (L2, centered under the IC). All AGND return currents must flow through this bridge.
- **PGND pour:** Small pour under BQ25120 for charger return currents. Connect to DGND at one point under the BQ25120. Do not connect directly to AGND.

### 5.11 Ground Moat (Zone A Isolation)

- On L2, excavate a 1 mm wide trench completely around Zone A, leaving only the 5 mm bridge under ADS1299.
- On L1 and L4, place a 0.5 mm wide GND trace around the zone boundary (connected to AGND) — this acts as a guard trace.
- Via-stitch the guard trace to the L2 AGND pour every 3 mm.

---

## 6. Critical Design Rules

### Rule 1: Single-Point Ground Connection
- AGND and DGND must connect at **exactly one point**: the 5 mm copper bridge under the ADS1299 on L2.
- Verify no other AGND-to-DGND connections exist (no vias bridging the moat, no components straddling both zones).
- **Check:** Run DRC clearance rule (min 1 mm gap between AGND and DGND everywhere except at the bridge).

### Rule 2: No Digital Traces in Analog Zone
- No digital traces (SPI, GPIO, I²C, UART) shall route through Zone A (ADS1299 area) except the SPI lines which must route to/from the ADS1299.
- SPI lines entering Zone A must go directly from the moat bridge to the ADS1299 pins — no detours.
- **Check:** Visual inspection + DRC region rule.

### Rule 3: Stim-to-EEG Trace Clearance
- All stimulation output traces (STIM_OUTA, STIM_OUTB, +5V_STIM, STIM_GND) must maintain ≥2 mm clearance (80 mil) from all EEG input traces (CH1P–CH8N).
- This applies on all layers (L1–L4).
- **Check:** Set a 2 mm net class clearance rule between `CH*` and `STIM_*` nets in DRC.

### Rule 4: BLE Antenna Keep-Out Zone
- No copper anywhere in the antenna keep-out area (55–80 mm X, 0–8 mm Y) on **any layer**.
- No components, traces, or vias in this zone (except the 50 Ω trace to the antenna itself).
- **Check:** Import keep-out area as a polygon cutout on all copper layers.

### Rule 5: USB-C 5V/2A Capability
- VBUS trace: 50 mil minimum width on L1 or L4, or use a copper pour.
- Connector rated for 3 A minimum.
- CC1/CC2: 5.1 kΩ ±1% pull-down to GND.
- ESD protection: TPD4E05U06 or equivalent on VBUS, CC1, CC2, D+, D− (if data lines used).

### Rule 6: Crystal Load Capacitance
- 32 MHz: C232 = C233 = 18 pF (CL = 12 pF, Cparasitic ≈ 6 pF → CL = (C232 × C233)/(C232 + C233) + Cparasitic = 9 + 6 = 15 pF → adjust to 18 pF as needed based on PCB parasitics).
- 32.768 kHz: C234 = C235 = 22 pF (CL = 12.5 pF, Cparasitic ≈ 6 pF → (22×22)/(44) + 6 = 11 + 6 = 17 pF → use 22 pF for margin).
- Crystal traces: 8 mil, keep under 5 mm length. No vias. Guard ring with GND around crystal.

### Rule 7: Decoupling Capacitor Placement
- **100 nF caps:** Place within 2 mm (preferably 1 mm) of the target power pin. Connect directly to the pin first, then via to power plane. Via to ground plane should be adjacent (not shared).
- **10 μF bulk caps:** Within 5 mm of the target IC.
- Capacitor loop area must be minimized. Use 0402 or 0603 packages.
- **Bad:** Daisy-chaining decoupling caps. Each cap must have its own via to the power/ground plane.

### Rule 8: Thermal Relief
- All through-hole pads (connectors, headers, test points): Use thermal relief spokes (4 spokes, 12 mil width).
- Battery connector: Thermal relief on all pads to prevent solder wicking during reflow.
- Thermal vias under QFN/DSBGA: **No thermal relief** — connect directly to the plane for maximum thermal conductivity.

### Rule 9: Fiducial Marks
- Place **3 fiducial marks** for pick-and-place alignment:
  - Top-left corner: (3 mm, 3 mm from board edge)
  - Top-right corner: (77 mm, 3 mm from board edge)
  - Bottom-right corner: (77 mm, 47 mm from board edge)
- Fiducial: 1 mm diameter copper circle on L1, with 3 mm diameter solder-mask opening.
- No traces, solder mask, or silkscreen within 3 mm of fiducial center.

### Rule 10: Solder Mask and Silkscreen
- **Solder mask:** Green, matte finish. Minimum web width 4 mil.
- **Silkscreen:** White, epoxy-based. Minimum text height 1.2 mm, line width 6 mil.
- All ICs require pin-1 indicator (dot or chamfer).
- All connectors require outline and pin numbering.
- All test points require label (net name).

---

## 7. Bill of Materials (Full)

### 7.1 Integrated Circuits

| Ref | Part Number | Description | Package | Qty | Manufacturer | Notes |
|-----|-------------|-------------|---------|-----|--------------|-------|
| U1 | ADS1299IPAG | 8-ch, 24-bit EEG ADC | TQFP-64 (10×10 mm) | 1 | Texas Instruments | |
| U2 | nRF5340-CKAA | Dual Cortex-M33, BLE 5.4 | aQFN-94 (7×7 mm) | 1 | Nordic Semi | |
| U3 | W25Q64JVSSIQ | 64 Mb SPI flash | SOIC-8 (5.3 mm) | 1 | Winbond | |
| U4 | ISO7740DW | Quad digital isolator | SOIC-16 wide (10.3×7.5 mm) | 1 | Texas Instruments | |
| U5 | DAC8562IDPW | 16-bit dual DAC, 2.5V ref | TSSOP-16 (5×4.4 mm) | 1 | Texas Instruments | |
| U6 | OPA2189IDGK | Dual precision op-amp | MSOP-8 (3×3 mm) | 1 | Texas Instruments | |
| U7 | ESP32-S3-WROOM-1-N16R8 | WiFi/BLE module, 16MB flash, 8MB PSRAM | Module (25.5×18 mm) | 1 | Espressif | |
| U8 | BQ25120AYFPT | Power management IC | DSBGA-20 (2.0×1.6 mm) | 1 | Texas Instruments | |

### 7.2 Passives — Resistors

| Ref | Value | Tolerance | Power | Package | Qty | Notes |
|-----|-------|-----------|-------|---------|-----|-------|
| R101–R108 | 100 Ω | ±1% | 0.1 W | 0603 | 8 | Anti-alias series resistors on EEG inputs |
| R102 | 10 kΩ | ±5% | 0.1 W | 0603 | 1 | BIAS_DRV series |
| R103, R107–R109 | 10 kΩ | ±5% | 0.1 W | 0603 | 4 | Pull-ups (ADS1299) |
| R104–R106 | 22 Ω | ±1% | 0.1 W | 0402 | 3 | SPI series (ADS1299) |
| R210 | 10 kΩ | ±5% | 0.1 W | 0402 | 1 | SWDIO pull-up |
| R211 | 10 kΩ | ±5% | 0.1 W | 0402 | 1 | SWCLK pull-down |
| R212 | 10 kΩ | ±5% | 0.1 W | 0402 | 1 | nRF5340 RESET pull-up |
| R501 | 10 kΩ | ±5% | 0.1 W | 0603 | 1 | DAC_SYNC pull-up |
| R502, R503 | 22 Ω | ±1% | 0.1 W | 0402 | 2 | SPI series (DAC) |
| R504 | 10 kΩ | ±5% | 0.1 W | 0603 | 1 | DAC LDAC pull-up |
| R505 | 10 kΩ | ±5% | 0.1 W | 0603 | 1 | DAC CLR pull-up |
| R506 | 10 kΩ | ±5% | 0.1 W | 0603 | 1 | DAC REF_EN pull-up |
| R601, R602 | 10 kΩ | ±0.1% | 0.1 W | 0805 | 2 | Howland input resistors — matched |
| R603, R604 | 10 kΩ | ±0.1% | 0.1 W | 0805 | 2 | Howland feedback resistors — matched |
| R605, R606 | 10 kΩ | ±0.1% | 0.1 W | 0805 | 2 | Howland input resistors (channel B) — matched |
| R607 | 100 Ω | ±1% | 0.25 W | 0805 | 1 | Stim output current limit |
| R401 | 10 kΩ | ±5% | 0.1 W | 0603 | 1 | ISO7740 EN pull-up |
| R701, R703, R704 | 10 kΩ | ±5% | 0.1 W | 0402 | 3 | ESP strapping/pull-up |
| R702 | 10 kΩ | ±5% | 0.1 W | 0402 | 1 | ESP strapping |
| R801, R802 | 4.7 kΩ | ±1% | 0.1 W | 0402 | 2 | I²C pull-up |
| R803 | 0 Ω | — | 0.1 W | 0603 | 1 | Battery sense jumper |
| R804, R805, R806 | 100 kΩ | ±5% | 0.1 W | 0402 | 3 | BQ25120 enable pull-ups |
| R807 | 10 kΩ | ±5% | 0.1 W | 0402 | 1 | BQ25120 PG pull-up |
| R808 | 1 MΩ | ±1% | 0.1 W | 0603 | 1 | BATMON divider top |
| R809 | 200 kΩ | ±1% | 0.1 W | 0603 | 1 | BATMON divider bottom |
| R810 | 10 kΩ NTC | ±1% | — | 0603 | 1 | Thermistor (BQ25120) |
| R811 | 1 kΩ | ±1% | 0.1 W | 0402 | 1 | TS pull-up |
| R901, R902 | 5.1 kΩ | ±1% | 0.1 W | 0603 | 2 | USB-C CC pull-down |
| R903 | 1 MΩ | ±5% | 0.1 W | 0603 | 1 | USB shield RC |

### 7.3 Passives — Capacitors

| Ref | Value | Dielectric | Tolerance | Voltage | Package | Qty | Notes |
|-----|-------|------------|-----------|---------|---------|-----|-------|
| C101, C103, C105, C117, C119, C121, C123, C125, C127, C129, C131, C133 | 100 nF | X7R | ±10% | 16 V | 0402 | 12 | ADS1299 AVDD decoupling |
| C102, C104, C110, C111, C118, C120, C122, C124, C126, C128, C130, C132, C134 | 10 μF | X5R | ±20% | 6.3 V | 0603 | 13 | ADS1299 AVDD bulk decoupling |
| C106 | 1 nF | NP0/C0G | ±5% | 16 V | 0603 | 1 | Anti-alias filter CH1 |
| C107, C108 | 1 μF | X7R | ±10% | 16 V | 0603 | 2 | BIAS caps |
| C109 | 1 μF | X7R | ±10% | 16 V | 0603 | 1 | REFN filter |
| C112, C113 | 1 μF | X7R | ±10% | 16 V | 0603 | 2 | VCAP2, VCAP3 |
| C114 | 100 nF | X7R | ±10% | 16 V | 0402 | 1 | VCAP4 |
| C115 | 100 nF | X7R | ±10% | 16 V | 0402 | 1 | DVDD decoupling |
| C116 | 10 μF | X5R | ±20% | 6.3 V | 0603 | 1 | DVDD bulk |
| C201, C203, C205, C207, C209, C211, C213, C224, C226, C228, C230, C236, C238, C240 | 100 nF | X7R | ±10% | 16 V | 0402 | 14 | nRF5340 VDD_IO decoupling |
| C202, C204, C206, C208, C210, C212, C214, C225, C227, C229, C231, C237, C239, C241 | 10 μF | X5R | ±20% | 6.3 V | 0603 | 14 | nRF5340 VDD_IO bulk |
| C215 | 100 nF | X7R | ±10% | 16 V | 0402 | 1 | nRF5340 RESET |
| C216, C218, C220, C222, C242 | 100 nF | X7R | ±10% | 16 V | 0402 | 5 | nRF5340 VDD/VREG decoupling |
| C217, C219, C221, C223, C243 | 10 μF | X5R | ±20% | 6.3 V | 0603 | 5 | nRF5340 VDD/VREG bulk |
| C232, C233 | 18 pF | NP0/C0G | ±5% | 50 V | 0603 | 2 | 32 MHz crystal load caps |
| C234, C235 | 22 pF | NP0/C0G | ±5% | 50 V | 0603 | 2 | 32.768 kHz crystal load caps |
| C501, C503, C504, C506, C508 | 100 nF | X7R | ±10% | 25 V | 0402 | 5 | DAC8562 decoupling |
| C502, C505, C507, C509 | 10 μF | X5R | ±20% | 10 V | 0603 | 4 | DAC8562 bulk |
| C601 | 100 nF | X7R | ±10% | 25 V | 0402 | 1 | OPA2189 decoupling |
| C602 | 10 μF | X5R | ±20% | 10 V | 0603 | 1 | OPA2189 bulk |
| C603 | 10 pF | NP0/C0G | ±5% | 50 V | 0603 | 1 | Howland stability |
| C401, C403 | 100 nF | X7R | ±10% | 16 V | 0402 | 2 | ISO7740 decoupling |
| C402, C404 | 10 μF | X5R | ±20% | 6.3 V | 0603 | 2 | ISO7740 bulk |
| C701 | 100 nF | X7R | ±10% | 16 V | 0402 | 1 | ESP RESET decoupling |
| C702 | 0.1 μF | X7R | ±10% | 16 V | 0402 | 1 | ESP VBUS |
| C801 | 10 μF | X5R | ±20% | 10 V | 0603 | 1 | BQ PMID |
| C802 | 1 μF | X7R | ±10% | 25 V | 0603 | 1 | BQ VBUS input |
| C803, C804 | 10 μF | X5R | ±20% | 10 V | 0603 | 2 | BQ VIN, BAT |
| C805, C807, C809 | 10 μF | X5R | ±20% | 6.3 V | 0603 | 3 | BQ LDO output bulk |
| C806, C808, C810 | 100 nF | X7R | ±10% | 16 V | 0402 | 3 | BQ LDO output decoupling |
| C901 | 10 nF | X7R | ±10% | 25 V | 0603 | 1 | USB shield RC |

### 7.4 Ferrite Beads / Inductors

| Ref | Value | Impedance | Current | Package | Qty | Manufacturer | Notes |
|-----|-------|-----------|---------|---------|-----|--------------|-------|
| FB1 | 600 Ω @ 100 MHz | 600 Ω | 100 mA | 0603 | 1 | Murata BLM18PG601SN1 | AVDD rail filter |
| FB2 | 600 Ω @ 100 MHz | 600 Ω | 100 mA | 0603 | 1 | Murata BLM18PG601SN1 | DVDD rail filter |
| FB501 | 100 Ω @ 100 MHz | 100 Ω | 200 mA | 0603 | 1 | Murata BLM18HG102SN1 | DAC DVDD filter |
| L1 | 2.7 nH | — | 300 mA | 0402 | 1 | Johanson | BLE balun inductor (match spec) |

### 7.5 Connectors

| Ref | Description | Pitch | Pins | Qty | Manufacturer / PN | Notes |
|-----|-------------|-------|------|-----|-------------------|-------|
| J1–J6 | Shielded wire-to-board, dry electrode | 2.54 mm | 3 (CH+, CH−, Shield) | 6 | JST XH or Wurth | Shield connects to AGND (Zone A) |
| J7 | Stim output connector | 2.54 mm | 2 (OUTA, GND) | 1 | JST XH or Keystone | Or 2-pin screw terminal |
| J8 | U.FL connector (WiFi) | — | 1 | 1 | Hirose U.FL-R-SMT-1 | Optional |
| J9 | USB-C connector, 24-pin, 16-pin mid-mount | 0.5 mm | 24 (16 used) | 1 | Amphenol 12401548E4#2A | 5 V / 3 A rated |
| J10 | LiPo battery connector | 2.0 mm | 2 (BAT+, BAT−) | 1 | JST PH (2-pin) | Molex 53047-0210 |
| J11 | 2nd ADS1299 expansion header | 2.54 mm | 14 (2×7) | 1 | Samtec TSW-107-07-G-D | Pin header or receptacle |
| J12 | tFUS expansion header | 2.54 mm | 6 (2×3) | 1 | Samtec TSW-103-07-G-D | |
| J13 | AI accelerator expansion header | 2.54 mm | 10 (2×5) | 1 | Samtec TSW-105-07-G-D | |
| J14 | External electrode expansion | 2.00 mm | 8 (1×8) | 1 | Samtec TMM-108-01-F-S | |
| J15 | Programming/debug header | 2.54 mm | 10 (2×5) | 1 | Samtec TSW-105-07-G-D | SWD + UART + GND + 3.3V |

### 7.6 Crystals / Oscillators

| Ref | Value | Package | Qty | Manufacturer / PN | Notes |
|-----|-------|---------|-----|-------------------|-------|
| X1 | 32 MHz, CL=12 pF, ESR≤40 Ω | 3.2×2.5 mm SMD (NX3225SA) | 1 | NDK NX3225SA-32.000M | Main MCU crystal |
| X2 | 32.768 kHz, CL=12.5 pF, ESR≤70 kΩ | 3.2×1.5 mm SMD (FC-135) | 1 | Epson FC-135 32.768 kHz | RTC crystal |

### 7.7 ESD Protection

| Ref | Part Number | Description | Package | Qty | Notes |
|-----|-------------|-------------|---------|-----|-------|
| D1–D12 | TPD4E05U06 | 4-ch ESD diode array, 0.5 pF | DRL-10 (SOT-553) | 3 | 3 arrays cover 12 lines: J1–J6 CH+, CH−, BIAS |
| D13 | TPD4E05U06 | ESD for USB-C | DRL-10 (SOT-553) | 1 | VBUS, D+, D−, CC1/CC2 shared |

### 7.8 Test Points

| Ref | Description | Package | Qty | Notes |
|-----|-------------|---------|-----|-------|
| TP1–TP4 | Test point, 1 mm loop | TP-1mm | 4 | GND, 3.3V_D, 3.3V_A, VBAT |
| TP5–TP7 | Test point, 1 mm loop | TP-1mm | 3 | NRF_SPI_SCLK, NRF_SPI_MOSI, NRF_DRDY |

### 7.9 Mechanical / Miscellaneous

| Ref | Description | Size | Qty | Notes |
|-----|-------------|------|-----|-------|
| M1–M4 | Mounting hole, M2, non-plated | 2.5 mm hole, 4 mm pad | 4 | 4 corners (3 mm from edge) |
| F1 | Resettable PTC fuse, 1 A, 6 V | 1812 | 1 | Battery protection |
| — | Fiducial marks (3×) | 1 mm Cu, 3 mm SM opening | 3 | See Rule 9 |

---

## 8. Assembly Notes

### 8.1 Reflow Profile (Mixed-Technology Board)

The board contains standard SMD (0402–SOIC) and fine-pitch components (TQFP-64, aQFN-94, DSBGA-20). Use a standard lead-free reflow profile with modifications for the DSBGA.

| Stage | Temp Range | Ramp Rate | Duration |
|-------|------------|-----------|----------|
| Pre-heat | 150–200 °C | 1.0–2.0 °C/s | 60–90 s |
| Soak | 200–217 °C | 0.5–1.0 °C/s | 60–90 s |
| Reflow | 217–245 °C (peak) | — | 45–75 s above 217 °C |
| Peak | 245 °C | — | 10–20 s (max 260 °C) |
| Cool | 245→180 °C | ≤3 °C/s | — |

**Specific notes:**
- **Solder paste:** SAC305 (Sn96.5/Ag3.0/Cu0.5) Type 4 (20–38 μm) for standard parts. Use Type 5 (10–25 μm) for DSBGA-20.
- **Stencil:** 0.125 mm (5 mil) thickness, electroformed. For DSBGA-20: aperture size 0.225 mm square (60% of pad size) to prevent bridging.
- **Pre-bake:** DSBGA components: bake at 125 °C for 24 hours before assembly (MSL 1 but sensitive to moisture).
- **Atmosphere:** N₂ recommended (<100 ppm O₂) to improve wetting on fine-pitch pads.

### 8.2 Manual Assembly Steps (Post-Reflow)

After reflow, the following components may require manual soldering or inspection:

1. **QFN-94 (nRF5340):** Inspect hidden solder joints under QFN with X-ray. If reflow voids >25% of pad, rework with hot air (350 °C, 30 s). Ensure thermal pad has good solder coverage (5×5 via array).

2. **DSBGA-20 (BQ25120):** X-ray inspection mandatory. Check for:
   - Solder bridging between balls (0.4 mm pitch).
   - Voids >30% pad area (reject).
   - Head-in-pillow defects (reflow profile must be validated on test board first).

3. **TQFP-64 (ADS1299):** Visual inspection under 10× microscope. Check for:
   - Solder bridging on 0.5 mm pitch leads.
   - Lifted leads (planarity issue).
   - Insufficient fillet height.

4. **Castellated module (ESP32-S3-WROOM-1):** Hand-solder or use a hot-bar soldering process. Ensure all 56 pads are wetted. Visual inspection — no underfill required.

5. **USB-C connector (J9):** Mid-mount connector. Solder first by hand (tack one pin), then reflow. Verify center ground pin (if present) is soldered — high current path.

6. **Mounting holes:** Use press-fit M2 standoffs after assembly. Do not solder (non-plated holes).

### 8.3 Inspection Criteria

| Component Type | Inspection Method | Acceptance Criteria |
|----------------|-------------------|---------------------|
| 0402/0603 passives | Visual (10×) | Centered on pad, fillet on both ends, no tombstoning, no dry joint |
| 0805/1206 passives | Visual (5×) | Same as above |
| SOIC-8/16, TSSOP-16, MSOP-8 | Visual (10×) | All leads wetted, no bridging, no lifted leads |
| TQFP-64 | Visual (20×) | All leads wetted, no bridging, no solder balls between leads. Solder fillet visible on heel, toe, and side of lead |
| QFN-94 | X-ray | >75% solder coverage on all pads. No bridging. Thermal pad >50% coverage. Voids <30% per pad |
| DSBGA-20 | X-ray | >50% solder coverage on all balls. No bridging. Head-in-pillow rejected. Voids <30% per ball |
| Module (castellated) | Visual (10×) | All 56 pads wetted, good fillet on side of module |
| USB-C | Visual + pull test | All pins soldered, connector sits flush with board |
| Through-hole connectors | Visual + push test | Pins protrude 1–2 mm past board, solder fills hole on both sides |

### 8.4 Test Points and Programming Locations

| Test Point | Location (X, Y) | Layer | Usage |
|------------|------------------|-------|-------|
| TP1 (GND) | (2 mm, 48 mm) | L1 | General purpose GND reference |
| TP2 (3.3V_D) | (2 mm, 45 mm) | L1 | Digital supply verification |
| TP3 (3.3V_A) | (2 mm, 2 mm) | L1 | Analog supply verification |
| TP4 (VBAT) | (78 mm, 2 mm) | L1 | Battery voltage monitor |
| TP5 (SCLK) | (30 mm, 20 mm) | L1 | SPI clock probe (Zone B) |
| TP6 (MOSI) | (32 mm, 20 mm) | L1 | SPI MOSI probe |
| TP7 (DRDY) | (28 mm, 22 mm) | L1 | ADS1299 data ready probe |

| Programming / Debug | Location (X, Y) | Interface | Target |
|---------------------|------------------|-----------|--------|
| J15 header | (25 mm, 46 mm) | SWD | nRF5340 |
| J15 header | (25 mm, 46 mm) | UART | ESP32-S3 |
| USB-C J9 | (80 mm, 25 mm) | USB / UART | ESP32-S3 (via CP210x if needed) |

### 8.5 Power-On Sequence

1. Connect USB-C power (5 V) or LiPo battery.
2. Verify BQ25120 PG (Power Good) LED/GPIO goes high within 100 ms.
3. Verify all three LDO outputs with multimeter on test points TP2, TP3, TP4.
4. nRF5340 boots automatically from power. Check SWCLK for activity (oscilloscope).
5. ESP32-S3 boots; UART TX should show bootloader messages.
6. Verify ADS1299 DRDY goes low at 250 Hz (measure with oscilloscope on TP7).
7. Configure BQ25120 via I²C for charge current and LDO voltages if not using defaults.

### 8.6 Compliance and Certification Notes

- **FCC / CE:** BLE and WiFi antenna traces must have passed impedance verification (TDR). The keep-out zone and ground stitching are critical for passing radiated emissions.
- **Medical (IEC 60601):** The ISO7740 provides 3 kV RMS isolation. Ensure isolation gap is maintained on all layers. If medical certification is required, add a physical slot under ISO7740.
- **ESD:** The TPD4E05U06 on electrode inputs provides ±15 kV air discharge protection. Ensure the GND connection of these diodes has a low-impedance path to AGND (vias directly to L2).

---

## Appendix A: Design Checklist

- [ ] PCB outline: 80 mm × 50 mm, ±0.1 mm tolerance, 1.6 mm thickness
- [ ] All four corners: M2 mounting holes (non-plated, 3 mm from edge)
- [ ] Stackup submitted to manufacturer per Section 1
- [ ] Impedance control: 50 Ω CPWG for antenna traces, verified by manufacturer
- [ ] Ground moat: 1 mm gap around Zone A on L2, single 5 mm bridge under ADS1299
- [ ] STIM_GND isolation: ≥2 mm gap between STIM_GND and DGND on L2
- [ ] Antenna keep-out: verified no copper on any layer in 55–80 × 0–8 mm area
- [ ] Stim-to-EEG clearance: ≥2 mm DRC rule configured
- [ ] SPI series termination resistors placed within 5 mm of source
- [ ] Crystal load caps within 1 mm of crystal pads
- [ ] All decoupling caps within 3 mm of IC (2 mm for 100 nF)
- [ ] Fiducials placed (3 corners)
- [ ] Solder mask defined (SMD) pads for QFN and DSBGA
- [ ] Thermal vias under nRF5340 and ADS1299 thermal pads
- [ ] Thermal relief on through-hole connectors and battery connector
- [ ] DRC complete: no clearance violations, no unconnected nets, no un-routed traces
- [ ] Silkscreen: all ICs have pin-1 marker, all connectors have outline
- [ ] BOM reviewed for availability and second-source options

---

## Appendix B: Reference Schematic Sheets

The KiCad project shall contain the following schematic sheets:

| Sheet # | Title | Contents |
|---------|-------|----------|
| 1 | Power Tree | BQ25120, LDOs, ferrite beads, power rail distribution, decoupling |
| 2 | Analog Front-End | ADS1299, electrode connectors, anti-aliasing filters, ESD, reference |
| 3 | Sensor MCU | nRF5340, crystals, SPI flash, BLE balun, SWD |
| 4 | Stimulation | ISO7740, DAC8562, OPA2189, Howland current pumps, output connector |
| 5 | AI MCU | ESP32-S3-WROOM-1, USB-C, antenna selection |
| 6 | Expansion | Headers for 2nd ADS1299, tFUS, AI accelerator, external electrode, programming |

---

*End of PCB Design Specification — NeuroResonator v1.0*
