import os
from setuptools import setup

# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

setup(
    name = "gcf-tools",
    version = "0.1",
    author = "GCF, NTNU, Trondheim - Arnar Flatberg, Geir Amund Svan Hasle",
    author_email = "arnar.flatberg@ntnu.no",
    description = ("In-house tool to create configs used in bio informatics pipelines at GCF, NTNU, Trondheim."),
    license = "BSD",
    url = "https://github.com/gcfntnu/gcf-tools",
    scripts = ['configmaker/configmaker.py', 'testdata/create_testdata.py'],
    packages=['configmaker', 'testdata'],
    install_requires=['wheel', 'glob', 're', 'pandas', 'argparse', 'logging', 'xlrd>=1.0.0'],
    setup_requires=['wheel', 'glob', 're', 'pandas', 'argparse', 'logging', 'xlrd>=1.0.0'],
)
