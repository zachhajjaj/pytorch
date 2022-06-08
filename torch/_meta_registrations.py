import torch
from torch import Tensor
from torch._prims import utils
from torch._prims.utils import check
from torch._prims.wrappers import out_wrapper_multi

from typing import List, Optional

meta_lib = torch.library.Library("aten", "IMPL", "Meta")

def toRealValueType(dtype):
    from_complex = {
        torch.complex32: torch.half,
        torch.cfloat: torch.float,
        torch.cdouble: torch.double
    }
    return from_complex.get(dtype, dtype)

# Implementations below are taken from https://github.com/albanD/subclass_zoo/blob/main/python_meta_tensor.py
@torch.library.impl(meta_lib, "index_select")
def meta_index_select(self, dim, index):
    result_size = list(self.size())
    if self.dim() > 0:
        result_size[dim] = index.numel()
    return self.new_empty(result_size)

@torch.library.impl(meta_lib, "index_select.out")
def meta_index_select_out(self, dim, index, out):
    torch._resize_output_(out, self.size(), self.device)
    return out.copy_(torch.index_select(self, dim, index))

@torch.library.impl(meta_lib, "max")
def meta_max(self):
    return self.new_empty(())

@torch.library.impl(meta_lib, "min")
def meta_min(self):
    return self.new_empty(())

def squareCheckInputs(self, f_name):
    assert self.dim() >= 2, f"{f_name}: The input tensor must have at least 2 dimensions."
    # TODO: I think the error message has the -2 and -1 swapped.  If you fix
    # it fix the C++ squareCheckInputs too
    assert self.size(-1) == self.size(-2), \
        f"{f_name}: A must be batches of square matrices, but they are {self.size(-1)} by {self.size(-2)} matrices"

def checkUplo(uplo: str):
    uplo_uppercase = uplo.upper()
    assert len(uplo) == 1 and uplo_uppercase == 'U' or uplo_uppercase == 'L', \
        f"Expected UPLO argument to be 'L' or 'U', but got {uplo}"

@torch.library.impl(meta_lib, "linalg_eigh")
def meta_linalg_eigh(self, uplo="L"):
    squareCheckInputs(self, "linalg_eigh")
    checkUplo(uplo)
    real_dtype = toRealValueType(self.dtype)
    assert self.dim() >= 2
    values = self.new_empty(self.shape, dtype=real_dtype)
    values.transpose_(-2, -1)
    vectors = self.new_empty(self.shape[:-1])
    return (values, vectors)

@torch.library.impl(meta_lib, "reflection_pad2d")
def meta_pad2d(self, padding):
    valid_dims = self.size(1) != 0 and self.size(2) != 0
    check(
        (self.ndim == 3 and valid_dims)
        or (self.ndim == 4 and valid_dims and self.size(3) != 0),
        lambda: f"3D or 4D (batch mode) tensor expected for input, but got: {self}"
    )
    if self.ndim == 4:
        nbatch, nplane, input_h, input_w = self.shape
    else:
        nbatch = 1
        nplane, input_h, input_w = self.shape

    pad_l, pad_r, pad_t, pad_b = padding

    output_h = input_h + pad_t + pad_b
    output_w = input_w + pad_l + pad_r

    if self.ndim == 3:
        return self.new_empty((nplane, output_h, output_w))
    else:
        return self.new_empty((nbatch, nplane, output_h, output_w))

@torch.library.impl(meta_lib, "dot")
def meta_dot(self, tensor):
    check(
        self.dim() == 1 and tensor.dim() == 1,
        lambda: f"1D tensors expected, but got {self.dim()}D and {tensor.dim()}D tensors"
    )
    return self.new_empty(())

@torch.library.impl(meta_lib, "var_mean.correction")
def meta_var_mean_correction(self, dim, *, correction, keepdim=False):
    dim = utils.reduction_dims(self.shape, dim)
    if keepdim:
        output_shape = tuple(self.shape[i] if i not in dim else 1 for i in range(self.ndim))
    else:
        output_shape = utils.compute_reduction_output_shape(self.shape, dim)
    result1 = self.new_empty(output_shape, dtype=toRealValueType(self.dtype))
    result2 = self.new_empty(output_shape)
    return result1, result2

@torch.library.impl(meta_lib, "inverse")
def meta_inverse(self):
    # Bug: https://github.com/pytorch/pytorch/issues/77498
    if self.numel() == 0:
        return torch.empty_like(self)
    r = self.new_empty(self.shape)
    r.transpose_(-2, -1)
    return r

@torch.library.impl(meta_lib, "bernoulli.out")
def meta_bernoulli(self, *, generator=None, out):
    torch._resize_output_(out, self.size(), self.device)
    return out

@torch.library.impl(meta_lib, "_adaptive_avg_pool2d")
def meta_adaptive_avg_pool2d(self, output_size):
    check(self.ndim == 3 or self.ndim == 4, lambda: f"Expected 3D or 4D tensor, but got {self.shape}")
    return self.new_empty(self.shape[:-2] + tuple(output_size))

@torch.library.impl(meta_lib, "_adaptive_avg_pool3d")
def meta_adaptive_avg_pool3d(self, output_size):
    check(self.ndim == 4 or self.ndim == 5, lambda: f"Expected 4D or 5D tensor, but got {self.shape}")
    return self.new_empty(self.shape[:-3] + tuple(output_size))

@torch.library.impl(meta_lib, "repeat_interleave.Tensor")
def meta_repeat_interleave_Tensor(repeats, output_size=None):
    if output_size is None:
        raise RuntimeError(
            "cannot repeat_interleave a meta tensor without output_size"
        )
    return repeats.new_empty(output_size)

@torch.library.impl(meta_lib, "index.Tensor")
def meta_index_Tensor(self, indices):
    check(indices, lambda: "at least one index must be provided")
    # aten::index is the internal advanced indexing implementation
    # checkIndexTensorTypes and expandTensors
    result: List[Optional[Tensor]] = []
    for i, index in enumerate(indices):
        if index is not None:
            check(
                index.dtype in [torch.long, torch.int8, torch.bool],
                lambda: "tensors used as indices must be long, byte or bool tensors"
            )
            if index.dtype in [torch.int8, torch.bool]:
                nonzero = index.nonzero()
                k = len(result)
                check(
                    k + index.ndim <= self.ndim,
                    lambda: f"too many indices for tensor of dimension {self.ndim}",
                    IndexError
                )
                for j in range(index.ndim):
                    check(
                        index.shape[j] == self.shape[k + j],
                        lambda: f"The shape of the mask {index.shape} at index {i} "
                                f"does not match the shape of the indexed tensor {self.shape} at index {k + j}",
                        IndexError
                    )
                    result.append(nonzero.select(1, j))
            else:
                result.append(index)
        else:
            result.append(index)
    indices = result
    check(len(indices) <= self.ndim, lambda: f"too many indices for tensor of dimension {self.ndim} (got {len(indices)})")
    # expand_outplace
    import torch._refs as refs  # avoid import cycle in mypy
    indices = list(refs._maybe_broadcast(*indices))
    # add missing null tensors
    while len(indices) < self.ndim:
        indices.append(None)

    # hasContiguousSubspace
    #   true if all non-null tensors are adjacent
    # See:
    # https://numpy.org/doc/stable/user/basics.indexing.html#combining-advanced-and-basic-indexing
    # https://stackoverflow.com/questions/53841497/why-does-numpy-mixed-basic-advanced-indexing-depend-on-slice-adjacency
    state = 0
    has_contiguous_subspace = False
    for index in indices:
        if state == 0:
            if index is not None:
                state = 1
        elif state == 1:
            if index is None:
                state = 2
        else:
            if index is not None:
                break
    else:
        has_contiguous_subspace = True

    # transposeToFront
    # This is the logic that causes the newly inserted dimensions to show up
    # at the beginning of the tensor, if they're not contiguous
    if not has_contiguous_subspace:
        dims = []
        transposed_indices = []
        for i, index in enumerate(indices):
            if index is not None:
                dims.append(i)
                transposed_indices.append(index)
        for i, index in enumerate(indices):
            if index is None:
                dims.append(i)
                transposed_indices.append(index)
        self = self.permute(dims)
        indices = transposed_indices

    # AdvancedIndex::AdvancedIndex
    # Now we can assume the indices have contiguous subspace
    # This is simplified from AdvancedIndex which goes to more effort
    # to put the input and indices in a form so that TensorIterator can
    # take them.  If we write a ref for this, probably that logic should
    # get implemented
    before_shape: List[int] = []
    after_shape: List[int] = []
    replacement_shape: List[int] = []
    for dim, index in enumerate(indices):
        if index is None:
            if replacement_shape:
                after_shape.append(self.shape[dim])
            else:
                before_shape.append(self.shape[dim])
        else:
            replacement_shape = list(index.shape)
    return self.new_empty(before_shape + replacement_shape + after_shape)

@out_wrapper_multi("L", "info")
def meta_linalg_cholesky_ex(input, upper=False, check_errors=False):
    check(input.ndim >= 2, lambda: f"expected matrix or batch of matrices, but got {input.ndim}-D tensor")
    check(
        utils.is_float_dtype(input.dtype) or utils.is_complex_dtype(input.dtype),
        lambda: f"expected float or complex tensor, but got {input.dtype}"
    )
    check(input.size(-1) == input.size(-2), lambda: f"expected square matrix but got {input.shape}")
    L = input.new_empty(input.size())
    L.transpose_(-2, -1)
    info_sizes = input.size()[:-2]
    info = input.new_empty(info_sizes, dtype=torch.int)
    return L, info

torch.library.impl(meta_lib, "linalg_cholesky_ex")(meta_linalg_cholesky_ex)
torch.library.impl(meta_lib, "linalg_cholesky_ex.L")(meta_linalg_cholesky_ex)
