import sys
with open("API/controller.c", "r") as f:
    c = f.read()

c = c.replace("return u * mrac_simplex.fade * mrac_inj.inj_alpha;", 'printf("u=%f, fade=%f, alpha=%f\\n", u, mrac_simplex.fade, mrac_inj.inj_alpha);\n    return u * mrac_simplex.fade * mrac_inj.inj_alpha;')

with open("tests/firmware_host/controller_copy.c", "w") as f:
    f.write(c)

