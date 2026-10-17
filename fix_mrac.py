import re

with open("API/mrac.h", "r") as f:
    text = f.read()

# AXES was changed from 4 to 6?
# In API/controller.h:
# typedef enum { CTRL_AXIS_PITCH = 0, CTRL_AXIS_ROLL = 1, CTRL_AXIS_YAW = 2, CTRL_AXIS_Z = 3,
#               CTRL_AXIS_X = 4, CTRL_AXIS_Y = 5 } ctrl_axis_e;   /* x/y: velocity-loop accel, Pos_Compute */
# Wait, I didn't change AXES in mrac.h, did I? No.
