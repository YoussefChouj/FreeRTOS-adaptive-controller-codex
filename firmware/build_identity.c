#include "stm32f4xx.h"

/* Safe fallback for GUI/manual builds. The headless build pipeline links a
 * strong generated build_id definition over this weak zero identity. */
__attribute__((weak, used)) volatile uint32_t build_id[4] = {0U, 0U, 0U, 0U};
