# Citations: every model, constant and algorithm in this repository, and where it comes from

This file is a gate, not a bibliography. `tools/check_citations.py` (CI group `audit`, suite `citations`) fails when
code carries a `[REF-nnn]` tag that has no row here, or when a row that code uses is not verified.

## Rules

1. **Cite where you implement.** The comment above an equation, a constant or an algorithm names its source as
   `[REF-nnn]` (and the section, equation or table: `[REF-008] eq. 5.6`). A datasheet value cites the datasheet row
   in `docs/datasheets/` or its BENCH id instead.
2. **Never cite from memory.** Before a row is used, an agent opens the DOI or URL, checks that title, authors, year
   and venue match, and writes `yes` plus the date and how it checked (`yes 2026-11-03 doi.org resolves, title
   matches`). A reference that cannot be found is not used; the model waits or gets another source.
3. **Read before you code.** The row says which section was implemented. If the paper's equation differs from what
   the code does (a simplification, another sign convention), the `Used for` column says so.
4. **Licences.** Papers are cited, not copied into the repository. Code taken from a paper's repository keeps its
   licence header and the licence is named in the row. PDFs go to the archive (`.archive/research/`), never into git.
5. **Datasheets count.** A part's datasheet is a reference like any other: id, revision, the table used.
6. Rows are never renumbered or deleted; a superseded row says `superseded by REF-nnn`.

## References

| Id | Reference | DOI / URL | Used for (file: what) | Verified |
|---|---|---|---|---|
| REF-001 | J. Sola, "Quaternion kinematics for the error-state Kalman filter", arXiv:1711.02508, 2017 | https://arxiv.org/abs/1711.02508 | sim/twin twin_math.h, rigid_body.h: Hamilton convention, quaternion kinematics q_dot = 0.5 q (0, w) | yes 2026-11-03 arXiv API: id, author, year and title match |
| REF-002 | D. Blackman, S. Vigna, "Scrambled Linear Pseudorandom Number Generators", ACM Trans. Math. Softw. 47(4), 2021 | https://doi.org/10.1145/3460772 | sim/twin rng.c: xoshiro256** and splitmix64, transcribed from the public-domain reference code (https://prng.di.unimi.it/) | yes 2026-11-03 Crossref: authors, ACM TOMS 47(4) 2021, DOI resolves |
| REF-003 | R. Featherstone, "Rigid Body Dynamics Algorithms", Springer, 2008 | https://doi.org/10.1007/978-1-4899-7560-7 | sim/twin rigid_body.c: Newton-Euler equations about the CoM with the gyroscopic term | yes 2026-11-03 Crossref: DOI registered to this book (Springer 2008) |
| REF-004 | D. E. Stewart, "Rigid-Body Dynamics with Friction and Impact", SIAM Review 42(1), 2000 | https://doi.org/10.1137/S0036144599360110 | sim/twin rigid_body.c: penalty (compliant) contact and regularised Coulomb friction, and their limits | yes 2026-11-03 Crossref: SIAM Rev. 42(1) 3-39, 2000 |
| REF-005 | N. El-Sheimy, H. Hou, X. Niu, "Analysis and Modeling of Inertial Sensors Using Allan Variance", IEEE Trans. Instrum. Meas. 57(1), 2008 | https://doi.org/10.1109/TIM.2007.908635 | sim/twin sensors.h: white noise and bias random walk, reading them off an Allan deviation plot | yes 2026-11-03 Crossref: IEEE TIM 57(1) 140-149, 2008 |
| REF-006 | ETH Zurich ASL, Kalibr wiki, "IMU Noise Model" | https://github.com/ethz-asl/kalibr/wiki/IMU-Noise-Model | sim/twin sensors.c: discrete-time noise from continuous densities, sigma_d = sigma_c / sqrt(dt) | yes 2026-11-03 page loads, title matches |
| REF-007 | A. Hughes, B. Drury, "Electric Motors and Drives: Fundamentals, Types and Applications", 5th ed., Newnes, 2019 | https://doi.org/10.1016/C2017-0-03226-3 | sim/twin actuators.h: averaged DC motor equations, copper resistance temperature coefficient | yes 2026-11-03 Crossref book record for ISBN 978-0-08-102615-1, DOI resolves to Elsevier |
| REF-008 | T. L. Bergman, A. S. Lavine, F. P. Incropera, D. P. DeWitt, "Fundamentals of Heat and Mass Transfer", 7th ed., Wiley, 2011 | ISBN 978-0-470-50197-9 | sim/twin thermal.h (lumped capacitance, ch. 5, Biot number), firmware/core ctrl.c winding observer, gpu kernel | yes 2026-11-03 Open Library: ISBN is the 7th ed., Wiley 2011 |
| REF-009 | JEDEC, JESD51 series, "Methodology for the Thermal Measurement of Component Packages (Single Semiconductor Device)" | https://www.jedec.org/standards-documents/docs/jesd-51 | sim/twin thermal.h: what datasheet theta_JA / theta_JC / psi_JT mean and the board they assume | yes 2026-11-03 document confirmed on the standards listing (standards.globalspec.com/std/939085/jedec-jesd-51); jedec.org blocks scripted fetches, open it in a browser |
| REF-010 | G. L. Plett, "Battery Management Systems, Volume I: Battery Modeling", Artech House, 2015 | ISBN 978-1-63081-023-8 | sim/twin battery.h: Thevenin equivalent circuit, OCV(SOC), coulomb counting | yes 2026-11-03 Open Library: ISBN is this book, Artech House 2015 |
| REF-011 | X. Hu, S. Li, H. Peng, "A comparative study of equivalent circuit models for Li-ion batteries", J. Power Sources 198, 2012 | https://doi.org/10.1016/j.jpowsour.2011.10.013 | sim/twin battery.h: why one RC pair is the usual accuracy / parameter-count compromise | yes 2026-11-03 Crossref: J. Power Sources 198, 359-367, 2012 |
| REF-012 | NOAA / NASA / USAF, "U.S. Standard Atmosphere, 1976" (NOAA-S/T 76-1562) | https://ntrs.nasa.gov/citations/19770009539 | sim/twin sensors.c: troposphere pressure law and its constants; test value 89874.6 Pa at 1000 m | yes 2026-11-03 NTRS record 19770009539, NOAA-S/T-76-1562, Oct 1976 |
| REF-013 | J. S. Steinhart, S. R. Hart, "Calibration curves for thermistors", Deep Sea Research and Oceanographic Abstracts 15(4), 1968 | https://doi.org/10.1016/0011-7471(68)90057-0 | sim/twin sensors.c and firmware/core ctrl.c: thermistor law (the beta model is its two-term form) | yes 2026-11-03 Crossref: 15(4) 497-503, Aug 1968 |
| REF-014 | C. Gomes, C. Thule, D. Broman, P. G. Larsen, H. Vangheluwe, "Co-simulation: A Survey", ACM Computing Surveys 51(3), 2018 | https://doi.org/10.1145/3179993 | docs: coupling the twin to a host engine at a fixed step (who integrates, what crosses the boundary) | yes 2026-11-03 Crossref: ACM CSUR 51(3), 2018 |
| REF-015 | B. Siciliano, L. Sciavicco, L. Villani, G. Oriolo, "Robotics: Modelling, Planning and Control", Springer, 2009 | https://doi.org/10.1007/978-1-84628-642-1 | sim/twin actuators.h (geared joint, reflected inertia), firmware/core ctrl.c (cascaded joint control, ch. 8) | yes 2026-11-03 Crossref: Springer London 2009 |
| REF-016 | P. Krause, O. Wasynczuk, S. Sudhoff, S. Pekarek, "Analysis of Electric Machinery and Drive Systems", 3rd ed., Wiley-IEEE Press, 2013 | https://doi.org/10.1002/9781118524336 | sim/twin actuators.h: PMSM model in the rotor dq frame, torque equation | yes 2026-11-03 Crossref: Wiley 2013, 3rd ed. (ISBN 978-1-118-02429-4) |

## How to add a row (the agent's procedure)

1. Find the source (see the skill's `references/research-citations.md`: where to search, what to prefer).
2. Open it. Check title, authors, year, venue, and that the section you implement says what you think it says.
3. Add the row with the next free id, the exact section in `Used for`, and `yes <date> <how>` in `Verified`.
4. Tag the code. Run `python -I tools/check_citations.py` (it lists tags without rows and used rows not verified).
