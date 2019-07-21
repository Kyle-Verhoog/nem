import os
import re
from setuptools import find_packages, setup

import fastentrypoints


def get_version(package):
    init_py = open(os.path.join(package, '__init__.py')).read()
    return re.search("__version__ = ['\"]([^'\"]+)['\"]", init_py).group(1)


version = get_version('nem')


setup(
    name='nem',
    version=version,
    description='mnemonics for your terminal',
    url='https://github.com/kyle-verhoog/nem',
    long_description='mnemonics for your terminal.',
    entry_points={
        'console_scripts': [
            'n = nem.nem:nem',
            'nem = nem.nem:nem',
        ],
    },
    long_description_content_type='text/markdown',
    packages=find_packages(exclude=['tests*']),
    install_requires=[
        'colouredlogs',
        'prompt-toolkit',
        'tabulate',
        'toml',
    ],
)
