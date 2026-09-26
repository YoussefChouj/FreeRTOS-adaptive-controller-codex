# Controller Interface & Pluggable Architecture

The firmware uses a runtime-selectable controller layer between the PID inner loops and the motor mixer (see `API/controller.h`). 
This interface allows switching between different control strategies without reflashing the firmware.

## Controller Selection

The active controller is determined by `g_ctrl_select`, which takes a `ctrl_id_e` value:

| ID | Name | Description | Status |
|----|------|-------------|--------|
| 0 | `CTRL_PID` | Pure PID nominal control, no adaptive correction. | Active |
| 1 | `CTRL_MRAC` | Standard Model Reference Adaptive Control (uses compiled basis). | Active (Default) |
| 2 | `CTRL_MRAC_STRUCT` | Structured uncertainty MRAC. | Reserved |
| 3 | `CTRL_MRAC_RBF` | RBF Neural Network MRAC. | Reserved |
| 4 | `CTRL_3LAYER` | 3-Layer Stack control. | Reserved |

**Note**: Slots with IDs 2, 3, and 4 are currently reserved and will be refused by the controller logic if selected.

## Runtime Switch (CMD 0x1F)

The ground station can request a controller change using the `CTRL_SELECT` command (0x1F).
The request is written to `g_ctrl_select_req`. 
To ensure safety, `Controller_CheckSwitch` only applies the requested controller when the drone is **disarmed**. This prevents dangerous transients in learned weights during flight.

Additionally, the `g_ctrl_axis_mask` allows applying the adaptive controller only to specific axes (bitmask of `ctrl_axis_e`):
- Bit 0: Pitch
- Bit 1: Roll
- Bit 2: Yaw
- Bit 3: Z-rate

A cleared bit sends pure PID on that axis.

## Relation to MRAC Simplex

The `mrac_simplex.variant` parameter (see `SIMPLEX.md`) provides an orthogonal, immediate override mechanism to fall back to pure PID control (`variant = 1`) even when MRAC is selected as the primary controller.

- **`g_ctrl_select` = `CTRL_PID`**: The drone runs on pure PID. `mrac_simplex` evaluates triggers but its output is bypassed.
- **`g_ctrl_select` = `CTRL_MRAC`, `mrac_simplex.variant` = 0**: The drone runs MRAC. Simplex triggers will freeze weights and fade out adaptive control if tripped.
- **`g_ctrl_select` = `CTRL_MRAC`, `mrac_simplex.variant` = 1**: The drone forces `fade = 0` (effectively PID control) regardless of MRAC being the selected controller.

The pluggable controller interface (`g_ctrl_select`) determines the structural control law applied (which adaptive algorithm to use), while `mrac_simplex` acts as a safety supervisor that can gracefully fade out the selected adaptive law.
