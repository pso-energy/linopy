#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Module containing all import/export functionalities."""
import logging
import os
import shutil
import time
from functools import partial, reduce
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

import numpy as np
import xarray as xr
from tqdm import tqdm
from xarray import apply_ufunc

logger = logging.getLogger(__name__)


ufunc_kwargs = dict(dask="parallelized", vectorize=True, output_dtypes=[object])

# IO functions
def to_float_str(da):
    """Convert a float array to a string array with lp like format for coefficients."""
    return apply_ufunc(lambda f: "%+f" % f, da.fillna(0), **ufunc_kwargs)


def to_int_str(da, nonnans=None):
    """Convert a int array to a string array."""
    return xr.apply_ufunc(lambda d: "%d" % d, da.fillna(0), **ufunc_kwargs)


def join_str_arrays(arraylist):
    """Join string array together (elementwise concatenation of strings)."""
    func = partial(np.add, dtype=object)  # np.core.defchararray.add
    return reduce(func, arraylist, "")


def str_array_to_file(array, fn):
    """Elementwise writing out string values to a file."""
    return xr.apply_ufunc(
        fn.write, array, dask="parallelized", vectorize=True, output_dtypes=[int],
    )


def objective_to_file(m, f):
    """Write out the objective of a model to a lp file."""
    f.write("min\nobj:\n")
    coef = m.objective.coeffs
    var = m.objective.vars

    nonnans = coef.notnull() & (var != -1)
    join = [to_float_str(coef), " x", to_int_str(var), "\n"]
    objective_str = join_str_arrays(join).where(nonnans, "")
    str_array_to_file(objective_str, f).compute()


def constraints_to_file(m, f):
    """Write out the constraints of a model to a lp file."""
    f.write("\n\ns.t.\n\n")
    labels = m.constraints.labels
    vars = m.constraints.vars
    coeffs = m.constraints.coeffs
    sign = m.constraints.sign
    rhs = m.constraints.rhs

    term_names = [f"{n}_term" for n in labels]

    nonnans = coeffs.notnull() & (vars != -1)
    join = [to_float_str(coeffs), " x", to_int_str(vars), "\n"]
    lhs_str = join_str_arrays(join).where(nonnans, "").reduce(np.sum, term_names)
    # .sum() does not work

    nonnans = nonnans.any(term_names) & (labels != -1) & sign.notnull() & rhs.notnull()

    join = [
        "c",
        to_int_str(labels),
        ": \n",
        lhs_str,
        sign,
        "\n",
        to_float_str(rhs),
        "\n\n",
    ]
    constraints_str = join_str_arrays(join).where(nonnans, "")
    str_array_to_file(constraints_str, f).compute()


def bounds_to_file(m, f):
    """Write out variables of a model to a lp file."""
    f.write("\nbounds\n")
    lower = m.variables.lower[m._non_binary_variables]
    labels = m.variables.labels[m._non_binary_variables]
    upper = m.variables.upper[m._non_binary_variables]

    nonnans = lower.notnull() & upper.notnull() & (labels != -1)
    join = [
        to_float_str(lower),
        " <= x",
        to_int_str(labels),
        " <= ",
        to_float_str(upper),
        "\n",
    ]
    bounds_str = join_str_arrays(join).where(nonnans, "")
    str_array_to_file(bounds_str, f).compute()


def binaries_to_file(m, f):
    """Write out binaries of a model to a lp file."""
    f.write("\nbinary\n")

    labels = m.binaries.labels
    nonnans = labels != -1
    binaries_str = join_str_arrays(["x", to_int_str(labels), "\n"]).where(nonnans, "")
    str_array_to_file(binaries_str, f).compute()


def to_file(m, fn):
    """Write out a model to a lp file."""
    if fn is None:
        fn = NamedTemporaryFile(
            suffix=".lp", prefix="linopy-problem-", dir=m.solver_dir,
        ).name

    if os.path.exists(fn):
        os.remove(fn)  # ensure a clear file

    with open(fn, mode="w") as f:

        start = time.time()

        objective_to_file(m, f)
        constraints_to_file(m, f)
        bounds_to_file(m, f)
        binaries_to_file(m, f)
        f.write("end\n")

        logger.info(f" Writing time: {round(time.time()-start, 2)}s")

    return fn


def to_block_files(m, fn):
    "Write out the linopy model to a block structured output."
    if fn is None:
        fn = TemporaryDirectory(prefix="linopy-problem-", dir=m.solver_dir).name

    path = Path(fn)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(exist_ok=True)

    m.calculate_block_maps()

    N = int(m.blocks.max())
    for n in range(N + 1):
        (path / f"block{n}").mkdir()

    vars = m.variables
    cons = m.constraints

    # Write out variables
    blocks = vars.ravel("blocks", filter_missings=True, compute=True)
    for key, suffix in zip(["labels", "lower", "upper"], ["x", "xl", "xu"]):
        arr = vars.ravel(key, filter_missings=True, compute=True)
        for n in tqdm(range(N + 1), desc=f"Write variable {key}"):
            arr[blocks == n].tofile(path / f"block{n}" / suffix)

    # Write out objective (uses variable blocks from above)
    coeffs = np.zeros_like(blocks)
    coeffs[np.asarray(m.objective.vars)] = np.asarray(m.objective.coeffs)
    for n in tqdm(range(N + 1), desc="Write objective"):
        coeffs[blocks == n].tofile(path / f"block{n}" / "c")

    # Write out rhs
    blocks = cons.ravel("blocks", filter_missings=True, compute=True)
    rhs = cons.ravel("rhs", filter_missings=True, compute=True)
    is_equality = cons.ravel(cons.sign == "==", filter_missings=True, compute=True)
    is_lower_bound = cons.ravel(cons.sign == ">=", filter_missings=True, compute=True)

    for n in tqdm(range(N + 1), desc="Write RHS"):
        is_blockn = blocks == n

        rhs[is_blockn & is_equality].tofile(path / f"block{n}" / "b")

        not_equality = is_blockn & ~is_equality
        is_lower_bound_sub = is_lower_bound[not_equality]
        rhs_sub = rhs[not_equality]

        lower_bound = np.where(is_lower_bound_sub, rhs_sub, -np.inf)
        lower_bound.tofile(path / f"block{n}" / "dl")

        upper_bound = np.where(~is_lower_bound_sub, rhs_sub, np.inf)
        upper_bound.tofile(path / f"block{n}" / "du")

    # Write out constraints
    conblocks = cons.ravel("blocks", "vars", filter_missings=True, compute=True)
    varblocks = cons.ravel("var_blocks", "vars", filter_missings=True, compute=True)
    is_equality = cons.ravel(
        cons.sign == "==", "vars", filter_missings=True, compute=True
    )

    is_varblock_0 = varblocks == 0
    is_conblock_L = conblocks == N

    for key, suffix in zip(["labels", "coeffs", "vars"], ["row", "data", "col"]):
        arr = cons.ravel(key, "vars", filter_missings=True, compute=True)
        for n in tqdm(range(N + 1), desc=f"Write constraint {key}"):
            is_conblock_n = conblocks == n
            is_varblock_n = varblocks == n

            mask = is_conblock_n & is_varblock_n
            arr[mask & is_equality].tofile(path / f"block{n}" / f"B_{suffix}")
            arr[mask & ~is_equality].tofile(path / f"block{n}" / f"D_{suffix}")

            mask = is_conblock_n & is_varblock_0
            arr[mask & is_equality].tofile(path / f"block{n}" / f"A_{suffix}")
            arr[mask & ~is_equality].tofile(path / f"block{n}" / f"C_{suffix}")

            mask = is_conblock_L & is_varblock_n
            arr[mask & is_equality].tofile(path / f"block{n}" / f"BL_{suffix}")
            arr[mask & ~is_equality].tofile(path / f"block{n}" / f"DL_{suffix}")


def non_bool_dict(d):
    """Convert bool to int for netCDF4 storing"""
    return {k: v if not isinstance(v, bool) else int(v) for k, v in d.items()}


def to_netcdf(m, *args, **kwargs):
    """
    Write out the model to a netcdf file.

    Parameters
    ----------
    m : linopy.Model
        Model to write out.
    *args
        Arguments passed to ``xarray.Dataset.to_netcdf``.
    **kwargs : TYPE
        Keyword arguments passed to ``xarray.Dataset.to_netcdf``.

    """

    def get_and_rename(m, attr, prefix=""):
        ds = getattr(m, attr)
        return ds.rename({v: prefix + attr + "-" + v for v in ds})

    vars = [
        get_and_rename(m.variables, attr, "variables_")
        for attr in m.variables.dataset_attrs
    ]
    cons = [
        get_and_rename(m.constraints, attr, "constraints_")
        for attr in m.constraints.dataset_attrs
    ]
    others = [get_and_rename(m, d) for d in m.dataset_attrs + ["objective"]]
    scalars = {k: getattr(m, k) for k in m.scalar_attrs}
    ds = xr.merge(vars + cons + others).assign_attrs(scalars)

    for k in ds:
        ds[k].attrs = non_bool_dict(ds[k].attrs)

    ds.to_netcdf(*args, **kwargs)


def read_netcdf(path, **kwargs):
    """
    Read in a model from a netcdf file.

    Parameters
    ----------
    path : path_like
        Path of the stored model.
    **kwargs
        Keyword arguments passed to ``xarray.load_dataset``.

    Returns
    -------
    m : linopy.Model

    """
    from linopy.model import Constraints, LinearExpression, Model, Variables

    m = Model()
    all_ds = xr.load_dataset(path, **kwargs)

    def get_and_rename(ds, attr, prefix=""):
        keys = [k for k in ds if k.startswith(prefix + attr + "-")]
        return ds[keys].rename({k: k[len(prefix + attr) + 1 :] for k in keys})

    attrs = Variables.dataset_attrs
    kwargs = {attr: get_and_rename(all_ds, attr, "variables_") for attr in attrs}
    m.variables = Variables(**kwargs, model=m)

    attrs = Constraints.dataset_attrs
    kwargs = {attr: get_and_rename(all_ds, attr, "constraints_") for attr in attrs}
    m.constraints = Constraints(**kwargs, model=m)

    for attr in m.dataset_attrs + ["objective"]:
        setattr(m, attr, get_and_rename(all_ds, attr))
    m.objective = LinearExpression(m.objective)

    for k in m.scalar_attrs:
        setattr(m, k, all_ds.attrs.pop(k))

    return m
