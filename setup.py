#!/usr/bin/env python

from setuptools import setup, find_packages

setup(
    name='fast_trader',
    version='0.1.0',
    packages=find_packages(),
    author='notmeor',
    author_email='kevin.inova@gmail.com',
    description='',
    include_package_data=True,
    package_data={'': ['config.yaml']},
    install_requires=[
        'protobuf>=3.6.1',
        'inflection']
)
