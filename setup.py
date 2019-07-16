import os
import re
from setuptools import find_packages, setup


def get_version(package):
    """
    Return package version as listed in `__version__` in `__init__.py`.
    This method prevents to import packages at setup-time.
    """
    init_py = open(os.path.join(package, '__init__.py')).read()
    return re.search("__version__ = ['\"]([^'\"]+)['\"]", init_py).group(1)


version = get_version('nem')


setup(
    entry_points={
        'console_scripts': [
            'n = nem.nem:nem',
        ],
    },
    description='mnemonics for your terminal',
    long_description='mnemonics for your terminal.',
    long_description_content_type='text/markdown',
    name='nem',
    packages=find_packages(exclude=['tests*']),
    url='https://github.com/kyle-verhoog/nem',
    version=version,
)
