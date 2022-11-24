#!/usr/bin/env python
# -*- encoding: utf8 -*-
#
# Copyright (c) 2022 ESET spol. s r.o.
# Author: Marc-Etienne M.Léveillé <leveille@eset.com>
# See LICENSE file for redistribution.

from setuptools import setup

with open('README.md', 'r') as readme:
    long_description = readme.read()

setup(
    name='jupyter-kernel-proxy',
    version='1.0.1',
    description='Jupyter kernel acting as a proxy to any other, already running, kernel.',
    long_description=long_description,
    long_description_content_type="text/markdown",
    author='Marc-Etienne M.Léveillé',
    author_email='leveille@eset.com',
    url='https://github.com/eset/jupyter-kernel-proxy',
    license="BSD",
    py_modules=[ "jupyter_kernel_proxy" ],
    python_requires=">=2.7",
    install_requires=[
        "jupyter-core",
        "pyzmq>=17",
        "tornado>=5",
        "six",
    ],
    classifiers=[
        "Environment :: Plugins",
        "License :: OSI Approved :: BSD License",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3",
    ],
)
