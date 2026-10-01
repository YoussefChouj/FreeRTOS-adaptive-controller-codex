import re, os
def compress_file(path):
    text = open(path).read()
    # Replace sequences of empty lines with a single one (or none if possible, but let's just use sed later)
    pass
