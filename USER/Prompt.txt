I am a beginner learning embedded C programming by studying a complete drone flight control program (STM32 + FreeRTOS).

For the code I'm about to show you, please create detailed notes suitable for copying into Obsidian with the following structure:

## Note Title Format:
The note title should be the filename or specific line range being analyzed:
- **Whole file**: `filename.c` (e.g., `Main.c`, `BSP.c`)
- **Single line**: `filename.c Line X` (e.g., `Main.c Line 7`, `pwm.c Line 25`)
- **Line range**: `filename.c Lines X-Y` (e.g., `Main.c Lines 5-18`, `BSP.c Lines 10-28`)

Start the note with a heading using this exact format as the title.

## Requirements:
1. **Beginner-Friendly Explanation**: Assume I'm new to embedded C. Explain concepts like pointers, structs, hardware registers, interrupts, etc. when they appear.

2. **Line-by-Line Breakdown**: Go through the code sequentially, explaining:
   - What each line does
   - Why it's needed in a drone context
   - How it connects to hardware (sensors, motors, timers, etc.)
   - **For external references** (functions/variables from other files): 
     - Search the actual codebase to find the implementation
     - Explain what it actually does based on the real code¡ªdo NOT guess
     - Provide accurate file location with line numbers: `Implemented in: [[file_name.c]] lines 45-78`
     - If it's a global variable, show where it's declared and defined

3. **Bit Manipulation & Operators**: Since embedded C uses heavy bit operations, when you encounter:
   - Bitwise operators (`&`, `|`, `^`, `~`, `<<`, `>>`)
   - Bit masking patterns (`&= ~`, `|=`, etc.)
   - Register manipulation
   
   Provide:
   - Brief explanation of the operator
   - Concrete example with binary representation
   - Intuitive analogy when possible
   - Common use case in embedded systems
   
   **Example format**:
   ```c
   RCC->APB1ENR |= RCC_APB1ENR_TIM3EN;
   
   Operator: |= (bitwise OR assignment)
   What it does: Set specific bits to 1 without changing other bits
   
   Binary example:
   Current value:  0b0001'0000  (only TIM5 enabled)
   Mask:           0b0000'0010  (TIM3EN bit)
   Result (|=):    0b0001'0010  (both TIM5 and TIM3 enabled)
   
   Analogy: Like turning ON specific light switches in a room¡ªyou flip the switches 
   you want ON, but don't touch the ones already in their current state.
   
   Common use: Enabling hardware peripherals without disabling others.
   ```
   
   **Other operator examples to include when relevant**:
   ```c
   // Clear bits (turn OFF)
   GPIOA->ODR &= ~(1 << 5);  // Clear bit 5 (turn LED off)
   Binary: 0b0010'0000 &= ~0b0010'0000 = 0b0000'0000
   
   // Toggle bits (flip state)
   GPIOA->ODR ^= (1 << 5);   // Toggle bit 5
   Binary: 0b0000'0000 ^ 0b0010'0000 = 0b0010'0000
   
   // Test bits (check if ON)
   if (GPIOA->IDR & (1 << 5)) { /* bit 5 is high */ }
   
   // Shift left (multiply by 2^n)
   value = 1 << 5;  // Same as: value = 1 * 2^5 = 32
   
   // Shift right (divide by 2^n)
   value = 64 >> 2;  // Same as: value = 64 / 2^2 = 16
   ```

4. **Key Points Section**: Summarize:
   - Purpose of this file/function in the overall drone system
   - Critical concepts introduced (e.g., "PWM generation", "sensor fusion", "PID control")
   - Common beginner pitfalls or gotchas

5. **Visual Aids** (when helpful):
   - ASCII diagrams showing data flow
   - Memory layouts for structs
   - Timing diagrams for interrupts/tasks
   - Bit field diagrams for registers

6. **Related Files**: Tell me which other files I should study next to understand the data flow (e.g., "This reads sensors ¡ú next see `algorithm.c` for filtering")

7. **Obsidian Links** (use `[[double brackets]]` for linking): Suggest ONLY essential links that represent actual dependencies and relationships. Focus on:
   - Files that call or are called by this code: `[[main.c]]` `[[imu_update.c]]`
   - Key functions defined or used: `[[xTaskCreate]]` `[[BSP_Init]]` `[[IMU_DataDeal_Task]]`
   - Important data structures shared across files: `[[TaskHandle_t]]` `[[IMU_Struct]]`
   - Critical constants/defines referenced elsewhere: `[[START_TASK_PRIO]]` `[[configTICK_RATE_HZ]]`
   
   **Linking Principles**:
   - Will I search for this term again across multiple notes? ¡ú Link it
   - Does this represent a real dependency in the codebase? ¡ú Link it
   - Is this just a local implementation detail? ¡ú Don't link it
   - Aim for 5-10 links maximum per note for meaningful graph views
   
   **Inline Tagging**: Add Obsidian links inline within explanations where they naturally appear, not just at the end. This makes notes more navigable while reading.
   
   **Example**:
   ```markdown
   The [[BSP_Init]] function calls [[LED_Init]] to configure GPIO pins, then [[PWM_TIM3_Init]] 
   sets up motor timers. The [[StartTask_Handler]] is passed to [[xTaskCreate]] for later 
   task management.
   ```
   
   Instead of just listing links at the bottom, weave them into the narrative so readers can 
   click through to related concepts as they learn.

## Format:
Structure the note with Markdown headings, code blocks with syntax highlighting, and clear sections. Keep explanations focused and avoid redundancy¡ªexplain each concept once where it's most relevant.

---

**Code to analyze:**
[Paste code here]

---

**Code to analyze:**
[Paste code here] 