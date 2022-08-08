Jupyter Kernel Proxy
====================

This Jupyter kernel serves as a proxy to any already running kernel. It is meant
to allow Jupyter Notebook to use kernels it cannot start itself, such as kernel
embedded in another applications.

Jupyter Kernel Proxy was initially created as a way to use Jupyter Notebook
using [IPyIDA](http://github.com/eset/ipyida/), but should work with any other
embedded or remote kernel.

Install
-------

Jupyter Kernel Proxy is on
[PyPi](https://pypi.org/project/jupyter-kernel-proxy/) and can be installed
using

    pip install jupyter-kernel-proxy

The kernelspec must also be installed using:

    python -m jupyter_kernel_proxy install

Usage
-----

In Jupyter Notebook, start a new notebook and choose "Existing session" as
kernel type.

When launched, the kernel proxy will try to connect to the last started kernel.
This is similar to what `jupyter console --existing` would do.

The kernel implements a magic `%proxy` command to allow connecting to other
running kernels. Only two subcommands are implemented: `list` and `connect`.

Here is an example usage:

    In  [ ]: %proxy list
               kernel-382cad4b-2286-4ea0-bcb7-fcc30039ac78.json (proxy)
             * kernel-55159.json (no name)
               kernel-55151d00-21e4-4ea9-8dfd-b0842e27cba1.json (no name)
               kernel-50889.json (no name)
    In  [1]: a = 5
    In  [ ]: %proxy connect 55151d00-21e4-4ea9-8dfd-b0842e27cba1
             Connecting to kernel-55151d00-21e4-4ea9-8dfd-b0842e27cba1.json
    In  [1]: a = 4
    In  [2]: a
    Out [2]: 4
    In  [ ]: %proxy connect kernel-55159.json
             Connecting to kernel-55159.json
    In  [2]: a
    Out [2]: 5
