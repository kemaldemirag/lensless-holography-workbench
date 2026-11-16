# U02 system requirements

## Document control

| Field | Value |
|---|---|
| Gate | `EXEC-G1` |
| Product | U02 `lensless-holography-workbench` |
| Baseline | `584486a4272e7a4a077db70d6688b4146a69f524` |
| Date | 2026-11-16 |
| Status | `RESEARCH_REFERENCE_ONLY` — ECAD/MBSE research draft; not an active U02 product requirements baseline |
| Test framework | Python standard-library `unittest` |

This document preserves a reviewable ECAD/MBSE research draft for a possible
future versioned control service and virtual LED/camera devices without
replacing the existing U02 numerical core. It is based on the accepted G0
reconciliation and the 16.11.2026 MBSE--ECAD annex to the 15.11.2026 workflow
and acceptance plan. It does not make device integration an active U02 product
obligation. `EXEC-G2` and the later device-integration gates are `DEFERRED`
unless they receive a separate development decision and authorization.

Normative statements use **shall**. Proposed test identifiers are stable
forward references for EXEC-G2 through EXEC-G5. Those tests do not exist at
G1 and therefore have status `PLANNED / NOT_RUN`; they must not be counted in
the current 164-test baseline.

## Scope and fixed boundaries

### Existing software that shall be preserved

| Responsibility | Existing implementation |
|---|---|
| File input and immutable snapshots | [`image_io.py`](../../src/lensless_holography_workbench/image_io.py), [`session.py`](../../src/lensless_holography_workbench/session.py) |
| Plane-wave propagation and depth scan | [`depth_scan.py`](../../src/lensless_holography_workbench/depth_scan.py) |
| Point-source reconstruction | [`point_source.py`](../../src/lensless_holography_workbench/point_source.py) |
| Numerical focus navigation | [`focus_metrics.py`](../../src/lensless_holography_workbench/focus_metrics.py), [`autofocus.py`](../../src/lensless_holography_workbench/autofocus.py) |
| Aligned-image comparison | [`comparison.py`](../../src/lensless_holography_workbench/comparison.py) |
| Session 1.0 export and verification | [`session.py`](../../src/lensless_holography_workbench/session.py), [`opened_session.py`](../../src/lensless_holography_workbench/opened_session.py), [`session.schema.json`](../../schemas/session.schema.json) |
| Existing file-based orchestration | [`desktop_workflow.py`](../../src/lensless_holography_workbench/desktop_workflow.py), [`cli.py`](../../src/lensless_holography_workbench/cli.py), [`windows_app.py`](../../src/lensless_holography_workbench/windows_app.py) |
| External U02-T2 fixture boundary | [`unal_fixture.py`](../../src/lensless_holography_workbench/unal_fixture.py), [`unal-k18-fixture.schema.json`](../../schemas/unal-k18-fixture.schema.json) |

The proposed `control/` and `virtual_device/` packages, their contracts, and
their tests are architectural allocations only. G1 does not implement them.

### Acceptance status freeze

| Acceptance item | Required G1 status | Closure condition |
|---|---|---|
| U02-T1 numerical reproduction | `TEKRARLANDI` | Existing K03 numerical evidence only; it is not physical-focus evidence |
| U02-T2 Windows comparison and session | `BEKLİYOR / NOT_EVALUATED` | Authorized UNAL pair, verified parameters, shared crop/intensity mapping, and a pre-registered threshold |
| U02-T3 Raspberry Pi acquisition | `SAHA VERİSİ GEREKİR / NOT_RUN` | Real Pi RAW frame and uniquely correlated device metadata |
| `U02-HW-G1` requirements/ICD traceability | `TASARIM` | G1 requirements plus a reviewed G2 ICD and complete test links |
| `U02-HW-G2` virtual-device behavior | `BEKLİYOR` | Executed ACK/NACK, timeout, repeat, and safe-off tests |
| `U02-HW-G3` ECAD evidence | `BEKLİYOR` | A prior hardware decision requiring a custom PCB, followed by actual ECAD evidence |
| `U02-HW-G4` current/optical output | `SAHA VERİSİ GEREKİR` | Calibrated physical measurement |
| `U02-HW-G5` trigger/exposure/RAW metadata | `SAHA VERİSİ GEREKİR` | Real-device evidence |

## System requirements

### System structure and compatibility

| Requirement | Normative statement | Allocation | Planned verification |
|---|---|---|---|
| `U02-SYS-001` | The system shall keep presentation, device orchestration, device adapters, hologram processing, and evidence/session management as separate responsibility blocks. | Existing and proposed architecture | `U02-CT-ARCH-001`, `U02-IT-CTL-001` |
| `U02-SYS-002` | The control service shall invoke the existing numerical-core APIs and shall not copy or fork their propagation, comparison, focus, or session algorithms. | Proposed control service; existing core | `U02-IT-IMG-001`, `U02-REG-CORE-001` |
| `U02-SYS-003` | Device-backed operation shall be opt-in and shall not change the default CLI, desktop workflow, TIFF, CSV, JSON, or reopen behavior. | Proposed control service; existing presentation/session blocks | `U02-CT-ARCH-001`, `U02-REG-CLI-001`, `U02-REG-SES-001` |
| `U02-SYS-004` | Virtual and future physical devices shall implement the same versioned `DeviceAdapter` lifecycle contract without exposing transport-specific behavior to the numerical core. | Proposed control and adapter blocks | `U02-CT-ADP-001`, `U02-IT-CTL-001` |
| `U02-SYS-005` | A complete virtual-device run shall execute without physical hardware, GPIO access, or network access. | Proposed virtual-device block | `U02-IT-SIM-001` |
| `U02-SYS-006` | The reconstruction model shall be selected explicitly as plane-wave or point-source and shall be recorded; the system shall not infer a model from filenames or device identity. | Proposed control service; existing processing core | `U02-CT-IMG-001`, `U02-IT-IMG-002` |

### LED control

| Requirement | Normative statement | Allocation | Planned verification |
|---|---|---|---|
| `U02-LED-001` | Every logical LED command shall carry a schema version, a `request_id` unique within its control session, operation, `timeout_ms`, and, for current-setting operations, a numeric value with unit `mA`; an idempotent retry shall reuse the original ID and identical payload. | Proposed LED contract and adapter | `U02-CT-LED-001` |
| `U02-LED-002` | A requested current shall be finite, non-negative, and within an explicitly configured bound; a physical maximum shall remain `DATASHEET_REQUIRED` until an identified device datasheet is approved. | Proposed LED contract | `U02-CT-LED-002`, `U02-CT-ACC-002` |
| `U02-LED-003` | Every LED response shall correlate to the command `request_id` and shall contain `ACK` or `NACK`, a closed device state, a closed error code when applicable, and a data-origin marker. | Proposed LED contract and adapter | `U02-CT-LED-003` |
| `U02-LED-004` | `ACK` shall mean that a syntactically and semantically valid command was accepted; commanded current, simulated current, measured current, and optical-output validation shall remain distinct fields and claims. | Proposed LED contract and evidence boundary | `U02-CT-LED-004`, `U02-IT-ACC-001` |
| `U02-LED-005` | Repeating the same `request_id` with the same payload shall be idempotent; reusing it with a different payload shall fail closed with a defined `NACK`. | Proposed LED adapter | `U02-CT-LED-005`, `U02-VT-LED-001` |
| `U02-LED-006` | Startup, normal close, cancellation, timeout, and fault handling shall leave the LED in an idempotent safe `OFF` state. | Proposed LED adapter and control service | `U02-VT-LED-002`, `U02-IT-SAFE-001` |

### Camera acquisition

| Requirement | Normative statement | Allocation | Planned verification |
|---|---|---|---|
| `U02-CAM-001` | Every logical capture request shall carry a schema version, a `request_id` unique within its control session, `timeout_ms`, `exposure_us`, gain value plus declared gain representation, and sensor-mode request; an idempotent retry shall reuse the original ID and identical payload. | Proposed camera contract and adapter | `U02-CT-CAM-001` |
| `U02-CAM-002` | Every successful capture shall create one unique `capture_id` bound to exactly one `request_id`; a failed capture shall not publish a partial frame record. | Proposed camera adapter and control service | `U02-CT-CAM-002`, `U02-IT-CTL-002` |
| `U02-CAM-003` | A capture record shall include `capture_id`, `request_id`, RFC 3339 UTC capture time, injected monotonic event time, `exposure_us`, gain value and representation, sensor mode, array shape, dtype/encoding, content identity, and data origin. | Proposed capture-record contract | `U02-CT-CAM-003`, `U02-IT-SES-001` |
| `U02-CAM-004` | The virtual camera shall produce the same frame bytes and metadata for the same validated inputs, seed, clock, and identifier source. | Proposed virtual camera | `U02-VT-CAM-001`, `U02-IT-REP-001` |
| `U02-CAM-005` | Invalid requests, duplicate-identifier conflicts, timeouts, and injected camera faults shall return defined errors and shall not silently create a second independent capture. | Proposed camera adapter | `U02-CT-CAM-004`, `U02-VT-CAM-002`, `U02-IT-SAFE-001` |

### Control service and lifecycle

| Requirement | Normative statement | Allocation | Planned verification |
|---|---|---|---|
| `U02-CTL-001` | The control service shall validate commands and parameters before invoking any adapter operation or numerical processing. | Proposed control service | `U02-CT-CTL-001`, `U02-IT-SAFE-001` |
| `U02-CTL-002` | A nominal run shall order LED command acceptance, camera capture, immediate LED safe-off, explicit model selection, existing-core processing, final adapter close, and atomic publication only through an evidence contract compatible with the selected backend. | Proposed control service | `U02-IT-CTL-001` |
| `U02-CTL-003` | The service shall correlate control-session, `request_id`, `capture_id`, device parameters, timestamps, processing model, and processing-session identity in one traceable chain. | Proposed control service and device-session envelope | `U02-IT-CTL-002`, `U02-IT-SES-001` |
| `U02-CTL-004` | Device-control state shall use a separate `ControlSessionState`; it shall not extend or reinterpret `DesktopWorkflow.WorkflowState`. | Proposed control service | `U02-CT-CTL-002` |
| `U02-CTL-005` | A stale, out-of-order, duplicate-conflicting, or contract-invalid device response shall be rejected fail closed. | Proposed control service | `U02-CT-CTL-003`, `U02-IT-CTL-003` |
| `U02-CTL-006` | Clock, identifier, seed, and fault behavior required for deterministic virtual runs shall be injected through explicit interfaces rather than read implicitly from global state. | Proposed control and virtual-device blocks | `U02-CT-CTL-004`, `U02-IT-REP-001` |
| `U02-CTL-007` | A timeout, cancellation, adapter fault, processing error, or publication error shall trigger safe shutdown and shall not publish a completed device-session envelope. | Proposed control service | `U02-IT-SAFE-001`, `U02-IT-SES-002` |

### Numerical processing

| Requirement | Normative statement | Allocation | Planned verification |
|---|---|---|---|
| `U02-IMG-001` | A captured frame entering the numerical core shall satisfy the existing non-empty, two-dimensional, finite-value, shape, and parameter validation rules. | Existing numerical core; proposed adapter bridge | `U02-CT-IMG-002`, `U02-IT-IMG-003` |
| `U02-IMG-002` | Plane-wave processing shall reuse `normalize_hologram`, `PreparedWavefront`, and the existing depth-scan/session path. | Existing `depth_scan.py` and `session.py` | `U02-IT-IMG-001`, `U02-REG-CORE-001` |
| `U02-IMG-003` | Point-source processing shall reuse the existing point-source backend only after explicit selection, shall remain separate from the plane-wave depth-scan contract, and shall not be represented as session 1.0 without a separately approved compatible evidence contract. | Existing `point_source.py`; future evidence decision | `U02-IT-IMG-002`, `U02-CT-SES-001`, `U02-REG-CORE-001` |
| `U02-IMG-004` | Autofocus output shall remain `NUMERICAL_NAVIGATION_ONLY` and shall not silently replace `selected_depth_um` or create an acceptance result. | Existing `autofocus.py`; proposed orchestration | `U02-IT-IMG-004`, `U02-CT-ACC-001` |
| `U02-IMG-005` | Comparison shall remain `NOT_EVALUATED` with a null threshold until an acceptance threshold and alignment/intensity procedure are registered before the run. | Existing `comparison.py` and session contract | `U02-IT-ACC-002`, `U02-REG-ACC-001` |

### Session and evidence

| Requirement | Normative statement | Allocation | Planned verification |
|---|---|---|---|
| `U02-SES-001` | `schemas/session.schema.json` version 1.0 and its `BEKLİYOR / NOT_EVALUATED` semantics shall remain unchanged by the device-integration work. | Existing session contract | `U02-CT-SES-001`, `U02-REG-SES-001` |
| `U02-SES-002` | Device provenance shall be stored in a separate, versioned, opt-in device-session envelope rather than as undeclared top-level fields in session 1.0. | Proposed evidence wrapper | `U02-CT-SES-002` |
| `U02-SES-003` | The envelope shall record its schema version, control-session ID, command/capture IDs, origin, command and response times, device parameters/states, processing model, terminal state, compatible evidence type, and error code when applicable. | Proposed device-session contract | `U02-CT-SES-003`, `U02-IT-SES-001` |
| `U02-SES-004` | For a plane-wave run using session 1.0, the envelope shall reference the processing-session directory by a safe relative path plus the SHA-256 values of its `session.json` and `sha256.json`; no new file shall be inserted into that strictly managed directory. | Proposed wrapper; existing `opened_session.py` | `U02-CT-SES-004`, `U02-IT-SES-001`, `U02-REG-SES-001` |
| `U02-SES-005` | After verified LED safe-off and adapter close, completed device-session publication shall be deterministic and atomic, and `device-sha256.json` shall cover every other managed file in the outer package without attempting to hash itself. | Proposed evidence wrapper | `U02-CT-SES-005`, `U02-IT-SES-002` |
| `U02-SES-006` | The absence of a device-session envelope shall not prevent an existing valid session 1.0 package from reopening. | Existing session reader; proposed opt-in wrapper | `U02-IT-SES-003`, `U02-REG-SES-001` |

### Simulation and claim provenance

| Requirement | Normative statement | Allocation | Planned verification |
|---|---|---|---|
| `U02-SIM-001` | Every virtual command response, measurement, frame, and device record shall carry the machine-readable origin `SIMULATED`. | Proposed virtual-device contracts | `U02-CT-SIM-001`, `U02-IT-ACC-001` |
| `U02-SIM-002` | Repeating a virtual run with the same canonical inputs, injected seed, clock, and identifiers shall reproduce the same ordered events, frame bytes, records, and hashes. | Proposed virtual devices and control service | `U02-VT-SIM-001`, `U02-IT-REP-001` |
| `U02-SIM-003` | `NACK`, timeout, LED fault, camera fault, and processing-abort scenarios shall be deterministically injectable and independently observable. | Proposed fault-injection layer | `U02-VT-SIM-002`, `U02-IT-SAFE-001` |
| `U02-SIM-004` | Virtual devices shall perform no physical I/O and shall not emit a physical-current, optical-output, physical-focus, real-camera, or customer-acceptance claim. | Proposed virtual devices; acceptance boundary | `U02-CT-SIM-002`, `U02-IT-ACC-001` |

### Acceptance and physical-test boundaries

| Requirement | Normative statement | Allocation | Planned verification |
|---|---|---|---|
| `U02-ACC-001` | Without the authorized UNAL reference pair and pre-registered comparison procedure and threshold, U02-T2 shall remain `BEKLİYOR` and comparisons shall remain `NOT_EVALUATED`. | Existing acceptance/session contracts | `U02-CT-ACC-001`, `U02-REG-ACC-001` |
| `U02-ACC-002` | Without a real Pi RAW frame and uniquely correlated exposure, gain, sensor mode, and timestamp, U02-T3 shall remain `SAHA VERİSİ GEREKİR / NOT_RUN`. | Acceptance boundary; future Pi adapter | `U02-CT-ACC-001`, `U02-IT-T3-001` |
| `U02-ACC-003` | An `ACK`, simulated measurement, software test result, or future ERC/DRC result shall not be represented as calibrated current, optical output, physical focus, or customer acceptance. | All proposed layers | `U02-CT-ACC-002`, `U02-IT-ACC-001` |
| `U02-ACC-004` | Physical limits and measured-value tolerances shall remain unresolved until their datasheet, calibration method, instrument, and acceptance owner are identified. | Future hardware decision and HIL work | `U02-CT-ACC-002`, `U02-IT-T3-001` |

### Verification infrastructure

| Requirement | Normative statement | Allocation | Planned verification |
|---|---|---|---|
| `U02-VER-001` | New contract, virtual-device, and integration tests shall use `unittest.TestCase`, `test_*.py`, and the existing discovery command without requiring a new test framework. | Future `tests/contracts`, `tests/unit`, `tests/integration` | `U02-CT-VER-001`, `U02-REG-SUITE-001` |
| `U02-VER-002` | Every requirement in this document shall map to at least one stable contract, virtual-device, integration, or regression test identifier and a future evidence record. | Requirements and future validation reports | `U02-CT-TRACE-001` |
| `U02-VER-003` | Existing regression tests shall be run unchanged after each implementation gate, and historical run counts shall not be merged with a new run. | Existing test suite and future gate reports | `U02-IT-VER-001`, `U02-REG-SUITE-001` |

## Forward verification catalogue

All entries in this table are `PLANNED / NOT_RUN` at G1. Exact filenames may
be refined in G2, but identifiers and intent are stable unless a reviewed
requirements change records the replacement.

| Test ID | Level | Planned verification intent |
|---|---|---|
| `U02-CT-ARCH-001` | Contract/static | Enforce allowed layer dependencies and opt-in integration boundary |
| `U02-CT-ADP-001` | Contract | Verify common adapter lifecycle and conformance |
| `U02-CT-LED-001` | Contract | Validate logical command identity, retry identity, `timeout_ms`, and `mA` fields |
| `U02-CT-LED-002` | Contract | Reject non-finite, negative, or out-of-bound current and preserve `DATASHEET_REQUIRED` |
| `U02-CT-LED-003` | Contract | Validate correlated ACK/NACK, closed state, error code, and origin fields |
| `U02-CT-LED-004` | Contract | Keep command acceptance, commanded value, simulated value, and physical measurement distinct |
| `U02-CT-LED-005` | Contract | Enforce idempotent retry and conflicting-ID NACK behavior |
| `U02-VT-LED-001` | Virtual-device unit | Verify duplicate retry does not reapply the LED operation |
| `U02-VT-LED-002` | Virtual-device unit | Verify startup, cancel, fault, timeout, and close reach idempotent `OFF` |
| `U02-CT-CAM-001` | Contract | Validate logical request identity, retry, timeout/exposure units, gain representation, and mode |
| `U02-CT-CAM-002` | Contract | Enforce one request-to-capture identity and no partial failed record |
| `U02-CT-CAM-003` | Contract | Validate capture metadata, UTC/monotonic time, array identity, and origin |
| `U02-CT-CAM-004` | Contract | Define invalid, duplicate-conflicting, timeout, and fault responses |
| `U02-VT-CAM-001` | Virtual-device unit | Verify deterministic frame bytes and metadata |
| `U02-VT-CAM-002` | Virtual-device unit | Verify deterministic camera timeout and fault injection |
| `U02-CT-CTL-001` | Contract | Reject invalid control inputs before adapter or numerical calls |
| `U02-CT-CTL-002` | Contract | Keep `ControlSessionState` independent of `WorkflowState` |
| `U02-CT-CTL-003` | Contract | Reject stale, out-of-order, duplicate-conflicting, and invalid responses |
| `U02-CT-CTL-004` | Contract | Require explicit injected clock, ID, seed, and fault providers |
| `U02-CT-IMG-001` | Contract | Require explicit plane-wave or point-source model selection |
| `U02-CT-IMG-002` | Contract | Enforce existing image, shape, finite-value, and parameter validation at the bridge |
| `U02-CT-SES-001` | Contract | Freeze session 1.0 data model and reject incompatible point-source representation |
| `U02-CT-SES-002` | Contract | Keep device provenance in a separate versioned outer envelope |
| `U02-CT-SES-003` | Contract | Validate envelope IDs, times, states, model, evidence type, origin, and errors |
| `U02-CT-SES-004` | Contract | Validate safe relative session path and exact inner JSON/manifest hashes |
| `U02-CT-SES-005` | Contract | Define atomic publication and non-self-referential outer hash coverage |
| `U02-CT-SIM-001` | Contract | Require `SIMULATED` on all virtual records and frames |
| `U02-CT-SIM-002` | Contract | Prohibit physical-I/O and physical-acceptance claims from virtual adapters |
| `U02-VT-SIM-001` | Virtual-device unit | Repeat canonical inputs with identical ordered records and hashes |
| `U02-VT-SIM-002` | Virtual-device unit | Inject each declared NACK, timeout, device fault, and abort deterministically |
| `U02-CT-ACC-001` | Contract | Freeze U02-T2 and U02-T3 status/result enums and prerequisites |
| `U02-CT-ACC-002` | Contract | Require datasheet, calibration, instrument, and owner before physical claims |
| `U02-CT-VER-001` | Contract/static | Verify `unittest` naming, base classes, packages, and discovery compatibility |
| `U02-CT-TRACE-001` | Contract/static | Detect duplicate or unmapped requirement and test identifiers |
| `U02-IT-CTL-001` | Integration | Execute nominal LED, capture, safe-off, processing, close, and publication order |
| `U02-IT-CTL-002` | Integration | Correlate control-session, command, capture, and processing evidence identities |
| `U02-IT-CTL-003` | Integration | Reject stale or invalid responses without advancing the run |
| `U02-IT-SAFE-001` | Integration | Fault/timeout/cancel paths, safe shutdown, and no completed partial publication |
| `U02-IT-SIM-001` | Integration | Hardware-free end-to-end virtual execution |
| `U02-IT-IMG-001` | Integration | Delegate plane-wave work to the existing depth-scan/session core |
| `U02-IT-IMG-002` | Integration | Delegate point-source work explicitly without using session 1.0 |
| `U02-IT-IMG-003` | Integration | Reject captured data that fails existing numerical input validation |
| `U02-IT-IMG-004` | Integration | Keep autofocus navigation from changing selected depth or acceptance |
| `U02-IT-SES-001` | Integration | Link a plane-wave device envelope to an unchanged inner session by hashes |
| `U02-IT-SES-002` | Integration | Leave no completed envelope on processing, shutdown, or publication failure |
| `U02-IT-SES-003` | Integration | Reopen a legacy session successfully when no device envelope exists |
| `U02-IT-REP-001` | Integration | Canonical repeated-run record and hash equality |
| `U02-IT-ACC-001` | Integration | Demonstrate that simulated and ACK evidence cannot produce a physical PASS |
| `U02-IT-ACC-002` | Integration | Preserve U02-T2 `BEKLİYOR / NOT_EVALUATED` through device processing |
| `U02-IT-T3-001` | Integration boundary | Confirm physical U02-T3 remains `NOT_RUN` without real evidence |
| `U02-IT-VER-001` | Integration/process | Run a new timestamped suite after a gate and keep historical results separate |
| `U02-REG-CORE-001` | Regression | Existing depth, point-source, focus, comparison, and K03 behavior |
| `U02-REG-CLI-001` | Regression | Existing file-based CLI and desktop defaults |
| `U02-REG-SES-001` | Regression | Existing TIFF/CSV/JSON/hash/reopen behavior |
| `U02-REG-ACC-001` | Regression | Existing `BEKLİYOR / NOT_EVALUATED` semantics |
| `U02-REG-SUITE-001` | Regression | Full `unittest discover` suite and environment evidence |

## G1 verification state

| Evidence | Result |
|---|---|
| Accepted baseline | HEAD `584486a4272e7a4a077db70d6688b4146a69f524` |
| Existing full suite at G0 | 164 run, 161 passed, 0 failed/errors, 3 condition-based skips |
| G1 implementation tests | `NOT_APPLICABLE` -- G1 changes documentation only |
| Planned CT/VT/IT tests | `NOT_RUN` -- implementation is not present or authorized |
| Physical/HIL tests | `NOT_RUN` |
