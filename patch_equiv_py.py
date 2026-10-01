import sys

with open("API/tests/run_mrac_equiv.py", "r") as f:
    content = f.read()

# We need to add a -DMRAC_EQUIV_NEW_TREE to the compile command for the NEW tree only
content = content.replace("cmd_new = ['gcc', '-std=c99', '-Wall', '-I', str(cwd), '-I', 'API/tests/stubs', '-lm']",
                          "cmd_new = ['gcc', '-std=c99', '-Wall', '-I', str(cwd), '-I', 'API/tests/stubs', '-lm', '-DMRAC_EQUIV_NEW_TREE']")

with open("API/tests/run_mrac_equiv.py", "w") as f:
    f.write(content)
