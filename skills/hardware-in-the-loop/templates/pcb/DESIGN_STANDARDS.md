# Hardware design standards (rule book for every board and harness)

Status: skeleton written at project set-up. The rules below are the method's defaults; section 0 holds the project decisions
the onboarding interview fills in. These are design targets, not certification claims.

Every rule has an id. The id is what a checker prints, what a review comment cites and what a waiver names. Relaxing a rule
needs a cited basis (a row of this file, an IPC table, the fab's capability page) written into the commit that relaxes it;
"the router could not make it" is not a basis. Rules marked **machine** are checked by a script (the pipeline's DRC today,
`pcb/tools/standards_check.py` once it exists, see the roadmap in docs/PCB.md); **manual** rules are a review checklist item.

## 0. Project decisions (filled by the onboarding interview; every board reads its values from here)

| Decision | Value | Decided by / date | Notes |
|---|---|---|---|
| Main power bus nominal voltage and window | (interview: e.g. "12 V, 9-16 V") | | sets DER-01 and TVS-01 classes |
| Highest voltage on any board | (interview) | | sets FAB-02 spacing row |
| Board class (IPC-6012 / IPC-A-610) | (interview: Class 2 general, Class 3 high reliability) | | sets FAB-01 annular ring |
| Conformal coating | (interview: none / acrylic / urethane / silicone) | | allows the coated spacing column in FAB-02 |
| Fab and assembly house | (interview: e.g. JLCPCB) | | its capability page is the FAB-* basis |
| Default layer count and stack-up | (interview) | | more layers are allowed; report the price delta |
| Operating ambient range | (interview) | | sets CUR-01 temperature rise budget |
| Enclosure sealing | (interview: sealed / vented) | | thermal budget, connector choice |

Until a row is decided, the rule that depends on it is reported as "undecided" by reviews, never silently assumed.

## 1. Protection (PROT, TVS)

| Id | Rule | Basis | Check |
|---|---|---|---|
| PROT-01 | No one-time (blow) fuses on any board. Use an eFuse / hot-swap controller (adjustable limit, thermal shutdown, auto-retry or latch) or, on small rails, a resettable PTC (with PROT-03). | A blown fuse is invisible to firmware: the system sees "the rail died" with no cause. Owner rule of the method. | machine: no fuse part class in the BOM / schematic (reference prefix F + a non-resettable MPN family) |
| PROT-02 | The FLT / PG (fault, power-good) output of every eFuse or hot-swap controller reaches an MCU input, or a connector pin an MCU reads, with a pull to the FAULT level so an open line reads "fault". | Firmware must see and log every trip; a floating fault line must fail safe. | machine: net trace from the pin (by symbol pin name) to an MCU or connector within one passive hop |
| PROT-03 | The load side of every PTC reaches an ADC input, comparator or GPIO within two passive hops (a divider is fine), so a trip is observable. | A tripped PTC drops the rail to the hold-current IR drop; without a sense line that looks like a dead board. | machine: net trace as PROT-02 |
| PROT-04 | Every protection event (eFuse trip, PTC trip, over-temperature, reverse polarity, brown-out) has a status bit in the node's telemetry and a latched flag cleared only by an explicit command; firmware retries a bounded number of times, then latches. | Same as PROT-02, on the firmware side. | manual (firmware review) |
| PROT-05 | Failure-safe defaults: enables and arm lines default to OFF through pulls; a cut wire disarms. | Single-fault safety. | manual |
| TVS-01 | A TVS (stand-off voltage at or above the rail's maximum, clamp below the downstream absolute maximum where a part exists) from every power pin and every off-board signal pin of every connector to ground, placed at the connector. | Connectors are where ESD and cable transients enter (IEC 61000-4-2 / 4-5 test methods). | machine (roadmap: standards_check.py): every connector pin net has a TVS / ESD part on it |
| TVS-02 | Protection coordination: every pin on a net a TVS protects (directly, or through a series part such as a PTC or ferrite that a fast transient passes) is rated, absolute maximum, at or above the TVS's maximum clamp voltage at its rated pulse current; derived nodes (a divider output) declare their worst case; the TVS stand-off is at or above the rail's operating maximum. | A clamp that conducts above a part's absolute maximum protects the connector, not the part (the clamp voltage, not the breakdown voltage, is what the part sees during a surge). | machine, implemented: `board_pipeline.py` design step; parts declare `abs_max_v` / `tvs` / `series`, nets `net_limits()`; an unrated supply pin is a finding |

## 2. Redundancy and contacts (RED)

| Id | Rule | Basis | Check |
|---|---|---|---|
| RED-01 | Every power net and every ground net lands on at least two contacts of every connector with three or more contacts; each contact alone carries the whole net current at 80 % or less of its rating. | A single fretted or cracked contact must not drop a rail (avionics practice; the 50 % connector derating of NASA/GSFC PD-ED-1201 with a failed-contact margin). | machine: count contacts per power / ground net per connector; ERROR below 2 |
| RED-02 | A ground pin within two pitches of every fast signal; each differential pair on adjacent pins. | Return-path continuity through the connector. | machine (pin map), manual for cables |

## 3. Derating (DER)

| Id | Rule | Basis | Check |
|---|---|---|---|
| DER-01 | Ceramic capacitors at or below 50 % of rated voltage (60 % for film, per the derating table); semiconductors at or below 80 % of their absolute-maximum voltage at the highest bus voltage of section 0; resistors at or below 50 % of rated power. | NASA/GSFC PD-ED-1201 "EEE Parts Derating" (public), derived from MIL-STD-975 / EEE-INST-002. | machine: BOM ratings against the rail table; manual for power |
| DER-02 | Every power part has at least one second source with stock recorded in the BOM data. | Supply resilience. | machine (BOM data) |

## 4. Current capacity (CUR)

| Id | Rule | Basis | Check |
|---|---|---|---|
| CUR-01 | Conductor cross-section from IPC-2152 charts (or the IPC-2221B fit `I = k * dT^0.44 * A^0.725` with k = 0.048, which is on the safe side for inner layers) at the temperature rise budget of section 0 and 1.25 x the design current; via arrays sized per via at the same rise. | IPC-2152 "Standard for Determining Current-Carrying Capacity in Printed Board Design" (paywalled; the 2221B fit is widely published). | machine: narrowest track per net with a documented design current |
| CUR-02 | Currents a 2 oz plane cannot carry, or feeds that must be serviceable, go on a wire, not wider copper. | Practical limit; repairability. | manual |

## 5. Fabrication (FAB) - the fab's capability is enforced as DRC rules

| Id | Rule | Basis | Check |
|---|---|---|---|
| FAB-01 | Annular ring at or above the board class minimum (IPC-6012 Class 2 / 3 per section 0) and at or above the fab's published minimum; the larger wins. | IPC-6012 (paywalled); the fab's capability page. | machine: KiCad DRC minimum annular width (set by the pipeline's rules dict) |
| FAB-02 | Conductor spacing from IPC-2221B Table 6-1 for the voltage between conductors (coated column only if section 0 says the boards are coated), never below the fab minimum. | IPC-2221B section 6.3 (paywalled; the table is widely reproduced). | machine: KiCad DRC clearance + net-class clearance for high-voltage nets |
| FAB-03 | Boards with switching regulators or fast edges use at least four layers with an unbroken ground plane next to the signal layer. | Return-path control (Ott, "Electromagnetic Compatibility Engineering"). | machine: layer count vs presence of switching parts |
| FAB-04 | Track width, clearance, via size, drill, hole-to-hole and copper-to-edge minimums equal to the fab capability page values or larger; they are written into the board's design rules by the pipeline, so KiCad's DRC enforces them. Severities are KiCad's defaults; no rule is ever demoted. | The fab's capability page (for JLCPCB: jlcpcb.com/capabilities). | machine: final DRC |

## 6. EMC (EMC)

| Id | Rule | Basis | Check |
|---|---|---|---|
| EMC-01 | Fast and switching traces run over an unbroken reference plane on the adjacent layer; no split under them. | Return-path theory (Ott; Johnson & Graham). | machine (plane fill under the net) |
| EMC-02 | A ground stitching via within 2 mm of every signal via on a fast net; edge stitching at a pitch of lambda/20 or less at the highest frequency of concern. | Return current follows the signal through layer changes. | machine: distance from each fast-net via to the nearest ground via |
| EMC-03 | Switching-regulator hot loop (input ceramic - high-side switch - low-side switch - ground) as small as the package allows: input ceramic within 4 mm of the VIN / GND pins, same layer, ground via beside its ground pad; SW node copper as small as the current allows. | TI SLVA773 "Reduce buck converter EMI and voltage stress by minimizing inductive parasitics" (public). | machine: distance VIN cap to regulator pins; SW track length |
| EMC-04 | Every connector signal pin has a filter element on its net (TVS, ferrite, series resistor or shunt capacitor). | Cable-borne emissions and susceptibility. | machine |
| EMC-05 | Antenna keep-outs from the module datasheet are copper-free on every layer; no switching node within 10 mm of an antenna. | Module vendor integration guides. | machine (keep-out rule areas) |

## 7. Mechanical (CLR)

| Id | Rule | Basis | Check |
|---|---|---|---|
| CLR-01 | Part bodies keep the project's mechanical clearance (section 0 or 1 mm by default) to enclosure walls, fasteners and moving parts, checked against the CAD with the board's `<board>_cad_fit.json`. Exceptions are written rows (CLR-EX-nn) with the reason. | Assembly tolerance stack-up. | machine in the CAD fit check |
| CLR-02 | Courtyards never overlap; copper keeps the fab's edge clearance. | KiCad DRC (courtyards_overlap, copper_edge_clearance). | machine: placement DRC and final DRC |

## 8. Wires and harnesses (WIRE)

| Id | Rule | Basis | Check |
|---|---|---|---|
| WIRE-01 | Wire type, gauge and insulation chosen per run from a documented table (current, temperature, flex life); bundles derated per SAE AS50881 / FAA AC 43.13-1B chapter 11 practice. | Public AC 43.13-1B. | manual + harness JSON review |
| WIRE-02 | Every connector has strain relief; wires are tied or bonded at the connector (IPC/WHMA-A-620 workmanship). | IPC/WHMA-A-620 (paywalled). | manual |

## 9. Board layout and look (STAGE, LOOK)

First pass. Method and reasons: the skill's `docs/PCB.md`, "Stages" and "Board look".

- **STAGE-01:** every part belongs to one named stage in `pcb/<board>/stages.json`, with its job, inputs and outputs.
- **STAGE-02:** one floorplan region per stage, in signal order where the board's shape allows; passives sit beside the pin they serve, inside their stage.
- **STAGE-03:** each stage has a rectangle and a title on silk, on each face it uses. No fake stages: say in the table when a group is just how the circuit is.
- **STAGE-04:** a first pass may be rough, but no board routes or lands before a reviewer who is not its author has checked every part against the stages.
- **LOOK-01:** every part keeps its reference designator on silk, placed by `pcb/tools/place_labels.py`. Never moved off silk to pass a check.

## 10. Review checklist (manual items, every board)

- resistor power and junction temperature at the worst-case ambient of section 0
- creepage on any input above 50 V
- shield termination style on finished cables
- ferrite and TVS placement at the connector, not mid-board
- the CPL rotations in the assembly house's preview
