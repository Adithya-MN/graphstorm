#!/usr/bin/env python
import io
import os
import re
from datetime import datetime
from setuptools import find_packages, setup


def read(*names, **kwargs):
    with io.open(os.path.join(os.path.dirname(__file__), *names),
                 encoding=kwargs.get("encoding", "utf8")) as fp:
        return fp.read()


def find_version(*file_paths):
    version_file = read(*file_paths)
    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]", version_file, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError("Unable to find version string.")


VERSION = find_version('src', 'graphstorm', '__init__.py')

if VERSION.endswith('dev'):
    VERSION = VERSION + datetime.today().strftime('%Y%m%d')

requirements = [
    'boto3',
    'h5py>=2.10.0',
    'scipy',
    'tqdm',
    'pyarrow>=3',
    'transformers>=4.3.0',
    'pandas',
    'pylint',
    'sklearn',
    'ogb',
    'psutil',
]

extensions = []
cmdclass = {}

setup(
    # Metadata
    name='graphstorm',
    version=VERSION,
    python_requires='>=3.6',
    description='Package for Graph Storm',
    long_description_content_type='text/markdown',
    license='Apache-2.0',

    # Package info
    packages=find_packages(where="src", exclude=(
        'tests',
    )),
    package_dir={"": "src"},
    package_data={'': [os.path.join('datasets', 'dataset_checksums', '*.txt')]},
    zip_safe=True,
    include_package_data=True,
    install_requires=requirements,
    ext_modules=extensions,
    cmdclass=cmdclass,
)
