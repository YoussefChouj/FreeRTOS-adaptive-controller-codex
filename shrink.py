with open("ground_station/service/tests/test_runner.py", "r") as f:
    lines = f.readlines()
out = []
for line in lines:
    if line.strip() != "":
        out.append(line)
with open("ground_station/service/tests/test_runner.py", "w") as f:
    f.writelines(out)
