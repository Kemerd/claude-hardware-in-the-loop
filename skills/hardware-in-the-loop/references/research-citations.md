# Research and citations: how the agent finds, verifies and records the science behind the twin

A twin is only as good as its models, and a model is only as good as its source. This is the procedure for turning
"we need a motor thermal model" into code that cites a verified source, plus a starting reading list by topic.

## 1. The procedure

1. **Name the phenomenon and the accuracy needed.** "Winding temperature within 5 K over a 10-minute duty cycle" is
   a research question; "thermal model" is not. Write it into the plan step.
2. **Datasheet first.** For a part's behaviour the datasheet (and its application notes) beats any paper: noise
   density, thermal resistance, torque constant, OCV curve. Save the relevant table as text in
   `docs/datasheets/<part>.md` with the document number and revision.
3. **Then the standard, then the textbook, then the paper.** A standard (JEDEC, IEC, ISO, MIL-STD, IEEE) when one
   defines the quantity; a textbook for the classical model; a paper for anything newer or sharper.
4. **Search** (in this order): Google Scholar or Semantic Scholar for the topic; arXiv for preprints; the publisher
   page through doi.org; OpenAlex (`https://api.openalex.org/works?search=...`) when a script needs metadata. Prefer
   a survey to find the canonical paper, then read the canonical paper.
5. **Verify.** Open the DOI or URL. Title, authors, year and venue must match what you write. Never cite from
   memory, never cite a paper you have not opened, never invent a DOI. If the source cannot be reached, say so and do
   not use it.
6. **Read the part you implement.** Write down the equation numbers. Note every simplification you make.
7. **Record.** A row in `CITATION.md` (id, reference, DOI / URL, `Used for` with the section, `Verified` with date
   and method). Tag the code `[REF-nnn]`. A short note per significant source in `docs/research/<REF-nnn>.md`:
   the question, the equations used, the assumptions, the validity range, what the bench must confirm.
8. **Validate.** A model with a source still gets a test against a closed-form case and, when hardware exists, a
   bench comparison (a BENCH register row). The citation says where the model came from; the test says it was
   transcribed correctly; the bench says it describes this hardware.
9. **Store PDFs in the archive** (`.archive/research/`), not in git (copyright), and note the licence of any code
   taken from a paper's repository.

`python -I tools/check_citations.py` is the gate: a `[REF-nnn]` tag without a row fails, a row used by code without
`yes` in `Verified` fails.

## 2. A starting reading list (verify each entry before citing it, as above)

The ids in brackets are the rows the template's `CITATION.md` already carries (each verified against Crossref,
arXiv, Open Library or the publisher on 2026-11-03; re-check before relying on one). The other entries were checked
for authors, title, venue and year on the same date; they are pointers for the agent's own reading, and a row is
added only after the agent has opened the work itself.

### Rigid bodies, integration, contact

- R. Featherstone, *Rigid Body Dynamics Algorithms*, Springer 2008 [REF-003]. Articulated bodies, spatial algebra.
- J. Sola, *Quaternion kinematics for the error-state Kalman filter*, arXiv:1711.02508 [REF-001]. Conventions.
- D. Baraff, A. Witkin, *Physically Based Modeling*, SIGGRAPH course notes (Pixar Graphics online library).
- E. Hairer, C. Lubich, G. Wanner, *Geometric Numerical Integration*, Springer 2006. Why symplectic integrators
  conserve energy; choosing a step method.
- D. E. Stewart, *Rigid-Body Dynamics with Friction and Impact*, SIAM Review 2000 [REF-004].
- D. E. Stewart, J. C. Trinkle, *An implicit time-stepping scheme for rigid body dynamics with inelastic collisions
  and Coulomb friction*, Int. J. Numer. Methods Eng. 1996. The basis of most game-engine contact solvers.
- E. Todorov, T. Erez, Y. Tassa, *MuJoCo: A physics engine for model-based control*, IROS 2012.
- M. Macklin, M. Muller, N. Chentanez, *XPBD: Position-based simulation of compliant constrained dynamics*,
  Motion in Games 2016. What Chaos-style position solvers do under the hood.

### Actuators

- A. Hughes, B. Drury, *Electric Motors and Drives*, Newnes [REF-007]. DC, brushless, steppers, drives.
- P. Krause et al., *Analysis of Electric Machinery and Drive Systems*, Wiley-IEEE 2013 [REF-016]. dq models.
- B. Siciliano et al., *Robotics: Modelling, Planning and Control*, Springer 2009 [REF-015]. Joints, gearing,
  motion control, force control.
- Texas Instruments and STMicroelectronics FOC application notes (search "field oriented control application note");
  the SimpleFOC documentation (docs.simplefoc.com) for the hobby-scale implementation details.
- B. Armstrong-Helouvry, P. Dupont, C. Canudas de Wit, *A survey of models, analysis tools and compensation methods
  for the control of machines with friction*, Automatica 1994. Friction beyond Coulomb + viscous.

### Sensors and estimation

- N. El-Sheimy et al., *Analysis and Modeling of Inertial Sensors Using Allan Variance*, IEEE TIM 2008 [REF-005].
- IEEE Std 952-1997, *IEEE Standard Specification Format Guide and Test Procedure for Single-Axis Interferometric
  Fiber Optic Gyros* (the Allan variance procedure most IMU datasheets follow); superseded by IEEE Std 952-2020,
  cite the edition you read.
- Kalibr wiki, *IMU Noise Model* [REF-006].
- O. J. Woodman, *An introduction to inertial navigation*, Univ. of Cambridge tech. report UCAM-CL-TR-696, 2007.
- R. Mahony, T. Hamel, J.-M. Pflimlin, *Nonlinear Complementary Filters on the Special Orthogonal Group*, IEEE TAC
  2008. Attitude estimation that fits on a microcontroller.
- R. E. Kalman, *A New Approach to Linear Filtering and Prediction Problems*, J. Basic Eng. 1960; and any modern
  textbook treatment (e.g. Y. Bar-Shalom et al., *Estimation with Applications to Tracking and Navigation*).
- C. Forster et al., *On-Manifold Preintegration for Real-Time Visual-Inertial Odometry*, IEEE T-RO 2017.
- Z. Zhang, *A flexible new technique for camera calibration*, IEEE TPAMI 2000 (camera intrinsics for rendered
  sensors in Unreal or Isaac).
- S. Foix, G. Alenya, C. Torras, *Lock-in Time-of-Flight (ToF) Cameras: A Survey*, IEEE Sensors J. 2011.
- J. S. Steinhart, S. R. Hart, *Calibration curves for thermistors*, Deep Sea Research 1968 [REF-013].

### Thermal, electrical, power

- Bergman, Lavine, Incropera, DeWitt, *Fundamentals of Heat and Mass Transfer* [REF-008].
- JEDEC JESD51 series [REF-009]; the part's own thermal application notes (for example, "semiconductor and IC
  package thermal metrics" notes from TI or Infineon) for theta / psi on real boards.
- Foster and Cauer thermal networks: the transient thermal impedance curves in MOSFET datasheets are Foster
  networks; search the vendor's application note on "thermal impedance Foster Cauer".
- G. L. Plett, *Battery Management Systems* Vol. I [REF-010]; X. Hu et al., J. Power Sources 2012 [REF-011];
  O. Tremblay, L.-A. Dessaint, *Experimental Validation of a Battery Dynamic Model for EV Applications*, World
  Electric Vehicle Journal 2009.
- U.S. Standard Atmosphere 1976 [REF-012]; ISO 2533 for the same ISA.

### Twins, co-simulation, sim-to-real

- C. Gomes et al., *Co-simulation: A Survey*, ACM Computing Surveys 2018 [REF-014]. Fixed-step coupling.
- The FMI standard (fmi-standard.org): if the twin must plug into Modelica / Simulink tool chains, an FMU wrapper of
  the C ABI is the standard route.
- J. Tobin et al., *Domain Randomization for Transferring Deep Neural Networks from Simulation to the Real World*,
  IROS 2017; X. B. Peng et al., *Sim-to-Real Transfer of Robotic Control with Dynamics Randomization*, ICRA 2018.
  The seed sweep and per-unit tolerance draws are the engineering version of this.
- S. Shah et al., *AirSim: High-Fidelity Visual and Physical Simulation for Autonomous Vehicles*, Field and Service
  Robotics 2017 (arXiv:1705.05065). An Unreal-hosted simulator with the physics outside the engine.
- V. Makoviychuk et al., *Isaac Gym: High Performance GPU-Based Physics Simulation For Robot Learning*,
  arXiv:2108.10470, 2021.
- N. Koenig, A. Howard, *Design and use paradigms for Gazebo, an open-source multi-robot simulator*, IROS 2004.
- M. Grieves, J. Vickers, *Digital Twin: Mitigating Unpredictable, Undesirable Emergent Behavior in Complex
  Systems*, in Transdisciplinary Perspectives on Complex Systems, Springer 2017 (online 2016),
  doi:10.1007/978-3-319-38756-7_4.

### Numerics and determinism

- D. Goldberg, *What Every Computer Scientist Should Know About Floating-Point Arithmetic*, ACM Computing Surveys
  1991. Why FMA contraction and fast-math break bit-identical traces.
- D. Blackman, S. Vigna, *Scrambled Linear Pseudorandom Number Generators*, ACM TOMS 2021 [REF-002].
- NVIDIA, *CUDA C++ Programming Guide* and *Floating Point and IEEE 754 Compliance for NVIDIA GPUs* (white paper):
  what -fmad=false, -prec-div and -prec-sqrt guarantee.

### Environment and build standards (when the build spec names them)

- MIL-STD-810H (environmental test methods: vibration, shock, temperature, humidity, salt fog), MIL-STD-461G (EMI /
  EMC), MIL-STD-704 (aircraft power), SAE AS22759 (the former MIL-W-22759 fluoropolymer "Tefzel" wire family),
  IEC 60529 (IP codes), IEC 60068 (environmental testing), ASTM B117 (salt spray), IPC-A-610 and IPC J-STD-001
  (assembly class 2 / 3), IPC-2221 (board design, conductor spacing and current). Cite the edition the project
  builds to; standards are bought or read at a library, so cite the document number and edition, and record which
  clause each requirement comes from.
