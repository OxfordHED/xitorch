import random
from collections import defaultdict
import torch
from torch.autograd import gradcheck, gradgradcheck
import xitorch as xt
from xitorch.grad.jachess import hess
from xitorch.optimize import rootfinder, equilibrium, minimize
from xitorch._tests.utils import device_dtype_float_test, assert_no_memleak

class DummyModule(xt.EditableModule):
    def __init__(self, A, addx=True, activation="sigmoid", sumoutput=False):
        super(DummyModule, self).__init__()
        self.A = A  # (nr, nr)
        self.addx = addx
        self.activation = {
            "sigmoid": torch.nn.Sigmoid(),
            "cos": torch.cos,
            "square": lambda x: x * x
        }[activation]
        self.sumoutput = sumoutput
        self.biasdiff = True

    def set_diag_bias(self, diag, bias):
        self.diag = diag
        self.bias = bias
        self.biasdiff = bias.requires_grad

    def forward(self, x):
        # x: (nbatch, nr)
        # diag: (nbatch, nr)
        # bias: (nbatch, nr)
        nbatch, nr = x.shape
        x = x.unsqueeze(-1)
        A = self.A.unsqueeze(0).expand(nbatch, -1, -1)  # (nbatch, nr, nr)
        A = A + torch.diag_embed(self.diag)  # (nbatch, nr, nr)
        y = torch.bmm(A, x).squeeze(-1)  # (nbatch, nr)
        yr = self.activation(2 * y) + 2 * self.bias
        if self.addx:
            yr = yr + x.squeeze(-1)
        if self.sumoutput:
            yr = yr.sum()
        return yr

    def getparamnames(self, methodname, prefix=""):
        return [prefix + "A", prefix + "diag"] + ([prefix + "bias"] if self.biasdiff else [])

class DummyNNModule(torch.nn.Module):
    def __init__(self, A, addx=True, activation="sigmoid", sumoutput=False):
        super(DummyNNModule, self).__init__()
        self.A = A
        self.addx = addx
        self.activation = {
            "sigmoid": torch.nn.Sigmoid(),
            "cos": torch.cos,
            "square": lambda x: x * x
        }[activation]
        self.sumoutput = sumoutput
        self.biasdiff = True

    def set_diag_bias(self, diag, bias):
        self.diag = diag
        self.bias = bias
        self.biasdiff = bias.requires_grad

    def forward(self, x):
        # x: (nbatch, nr)
        # diag: (nbatch, nr)
        # bias: (nbatch, nr)
        nbatch, nr = x.shape
        x = x.unsqueeze(-1)
        A = self.A.unsqueeze(0).expand(nbatch, -1, -1)  # (nbatch, nr, nr)
        A = A + torch.diag_embed(self.diag)  # (nbatch, nr, nr)
        y = torch.bmm(A, x).squeeze(-1)  # (nbatch, nr)
        yr = self.activation(2 * y) + 2 * self.bias
        if self.addx:
            yr = yr + x.squeeze(-1)
        if self.sumoutput:
            yr = yr.sum()
        return yr

class DummyNNInEditableModule(xt.EditableModule):
    # test to see if it still works if torch nn module in EditableModule

    def __init__(self, A, addx=True, activation="sigmoid", sumoutput=False):
        self.module = DummyNNModule(A, addx=addx, activation=activation, sumoutput=sumoutput)

    def set_diag_bias(self, diag, bias):
        self.module.set_diag_bias(diag, bias)

    def forward(self, x):
        return self.module.forward(x)

    def getparamnames(self, methodname, prefix=""):
        nnprefix = prefix + "module"  # torch nn prefix does not have no dot at the end while ours does
        return [name for (name, param) in self.module.named_parameters(prefix=nnprefix)]

class DummyModuleExplicit(xt.EditableModule):
    def __init__(self, addx=True):
        super(DummyModuleExplicit, self).__init__()
        self.addx = addx
        self.activation = torch.nn.Sigmoid()

    def forward(self, x, A, diag, bias):
        nbatch, nr = x.shape
        x = x.unsqueeze(-1)
        A = A.unsqueeze(0).expand(nbatch, -1, -1)
        A = A + torch.diag_embed(diag)
        y = torch.bmm(A, x).squeeze(-1)
        yr = self.activation(2 * y) + 2 * bias
        if self.addx:
            yr = yr + x.squeeze(-1)
        return yr

    def getparamnames(self, methodname, prefix=""):
        return []

@device_dtype_float_test(only64=True, additional_kwargs={
    "clss": [DummyModule, DummyNNModule, DummyNNInEditableModule],
})
def test_rootfinder(dtype, device, clss):
    torch.manual_seed(100)
    random.seed(100)

    nr = 3
    nbatch = 2
    fwd_options = {
        "method": "broyden1",
        "f_tol": 1e-9,
        "alpha": -0.5,
    }

    A    = torch.nn.Parameter((torch.randn((nr, nr)) * 0.5).to(dtype).requires_grad_())
    diag = torch.nn.Parameter(torch.randn((nbatch, nr)).to(dtype).requires_grad_())
    bias = torch.nn.Parameter(torch.zeros((nbatch, nr)).to(dtype).requires_grad_())
    y0 = torch.randn((nbatch, nr)).to(dtype)

    model = clss(A, addx=True)
    model.set_diag_bias(diag, bias)
    y = rootfinder(model.forward, y0, **fwd_options)
    f = model.forward(y)
    assert torch.allclose(f * 0, f)

    def getloss(A, y0, diag, bias):
        model = clss(A, addx=True)
        model.set_diag_bias(diag, bias)
        y = rootfinder(model.forward, y0, **fwd_options)
        return y

    gradcheck(getloss, (A, y0, diag, bias))
    gradgradcheck(getloss, (A, y0, diag, bias))

@device_dtype_float_test(only64=True, additional_kwargs={
    "clss": [DummyModule, DummyNNModule],
})
def test_equil(dtype, device, clss):
    torch.manual_seed(100)
    random.seed(100)

    nr = 3
    nbatch = 2
    fwd_options = {
        "method": "broyden1",
        "f_tol": 1e-9,
        "alpha": -0.5,
    }
    bck_options = {
        "method": "cg",
    }

    A    = torch.nn.Parameter((torch.randn((nr, nr)) * 0.5).to(dtype).requires_grad_())
    diag = torch.nn.Parameter(torch.randn((nbatch, nr)).to(dtype).requires_grad_())
    bias = torch.nn.Parameter(torch.zeros((nbatch, nr)).to(dtype).requires_grad_())
    y0 = torch.randn((nbatch, nr)).to(dtype)

    model = clss(A, addx=False)
    model.set_diag_bias(diag, bias)
    y = equilibrium(model.forward, y0, bck_options=bck_options, **fwd_options)
    f = model.forward(y)
    assert torch.allclose(y, f)

    def getloss(A, y0, diag, bias):
        model = clss(A, addx=False)
        model.set_diag_bias(diag, bias)
        y = equilibrium(model.forward, y0, bck_options=bck_options, **fwd_options)
        return y

    gradcheck(getloss, (A, y0, diag, bias))
    gradgradcheck(getloss, (A, y0, diag, bias))

@device_dtype_float_test(only64=True, additional_kwargs={
    "bias_is_tensor": [True, False]
})
def test_rootfinder_with_params(dtype, device, bias_is_tensor):
    torch.manual_seed(100)
    random.seed(100)

    nr = 3
    nbatch = 2
    fwd_options = {
        "method": "broyden1",
        "f_tol": 1e-9,
        "alpha": -0.5,
    }

    clss = DummyModuleExplicit
    A    = (torch.randn((nr, nr)) * 0.5).to(dtype).requires_grad_()
    diag = torch.randn((nbatch, nr)).to(dtype).requires_grad_()
    if bias_is_tensor:
        bias = torch.zeros((nbatch, nr)).to(dtype).requires_grad_()
    else:
        bias = 0.0
    y0 = torch.randn((nbatch, nr)).to(dtype)

    model = clss(addx=True)
    y = rootfinder(model.forward, y0, (A, diag, bias), **fwd_options)
    f = model.forward(y, A, diag, bias)
    assert torch.allclose(f * 0, f)

    def getloss(y0, A, diag, bias):
        model = clss(addx=True)
        y = rootfinder(model.forward, y0, (A, diag, bias), **fwd_options)
        return y

    gradcheck(getloss, (y0, A, diag, bias))
    gradgradcheck(getloss, (y0, A, diag, bias))

@device_dtype_float_test(only64=True, additional_kwargs={
    "clss": [DummyModule, DummyNNModule],
})
def test_minimize(dtype, device, clss):
    torch.manual_seed(400)
    random.seed(100)

    nr = 3
    nbatch = 2

    A    = torch.nn.Parameter((torch.randn((nr, nr)) * 0.5).to(dtype).requires_grad_())
    diag = torch.nn.Parameter(torch.randn((nbatch, nr)).to(dtype).requires_grad_())
    # bias will be detached from the optimization line, so set it undifferentiable
    bias = torch.zeros((nbatch, nr)).to(dtype)
    y0 = torch.randn((nbatch, nr)).to(dtype)
    fwd_options = {
        "method": "broyden1",
        "max_niter": 50,
        "f_tol": 1e-9,
        "alpha": -0.5,
    }
    activation = "square"  # square activation makes it easy to optimize

    model = clss(A, addx=False, activation=activation, sumoutput=True)
    model.set_diag_bias(diag, bias)
    y = minimize(model.forward, y0, **fwd_options)

    # check the grad (must be close to 1)
    with torch.enable_grad():
        y1 = y.clone().requires_grad_()
        f = model.forward(y1)
    grady, = torch.autograd.grad(f, (y1,))
    assert torch.allclose(grady, grady * 0)

    # check the hessian (must be posdef)
    h = hess(model.forward, (y1,), idxs=0).fullmatrix()
    eigval, _ = torch.symeig(h)
    assert torch.all(eigval >= 0)

    def getloss(A, y0, diag, bias):
        model = clss(A, addx=False, activation=activation, sumoutput=True)
        model.set_diag_bias(diag, bias)
        y = minimize(model.forward, y0, **fwd_options)
        return y

    gradcheck(getloss, (A, y0, diag, bias))
    gradgradcheck(getloss, (A, y0, diag, bias))

############## forward methods test ##############
@device_dtype_float_test(only64=True, additional_kwargs={
    "method": ["broyden1", "broyden2", "linearmixing"],
})
def test_rootfinder_methods(dtype, device, method):
    torch.manual_seed(100)
    random.seed(100)
    dtype = torch.float64

    nr = 3
    nbatch = 2
    default_fwd_options = {
        "f_tol": 1e-9,
        "alpha": -0.5,
    }
    # list the methods and the options here
    options = {
        "broyden1": default_fwd_options,
        "broyden2": default_fwd_options,
        "linearmixing": default_fwd_options,
    }[method]

    A    = torch.nn.Parameter((torch.randn((nr, nr)) * 0.5).to(dtype).requires_grad_())
    diag = torch.nn.Parameter(torch.randn((nbatch, nr)).to(dtype).requires_grad_())
    bias = torch.nn.Parameter(torch.zeros((nbatch, nr)).to(dtype).requires_grad_())
    y0 = torch.randn((nbatch, nr)).to(dtype)

    fwd_options = {**options, "method": method}
    model = DummyModule(A, addx=True)
    model.set_diag_bias(diag, bias)
    y = rootfinder(model.forward, y0, **fwd_options)
    f = model.forward(y)
    assert torch.allclose(f * 0, f)

@device_dtype_float_test(only64=True, additional_kwargs={
    "method": ["broyden1", "broyden2", "linearmixing"],
})
def test_equil_methods(dtype, device, method):
    torch.manual_seed(100)
    random.seed(100)

    nr = 3
    nbatch = 2
    default_fwd_options = {
        "f_tol": 1e-9,
        "alpha": -0.5,
    }
    # list the methods and the options here
    options = {
        "broyden1": default_fwd_options,
        "broyden2": default_fwd_options,
        "linearmixing": default_fwd_options,
    }[method]

    A    = torch.nn.Parameter((torch.randn((nr, nr)) * 0.5).to(dtype).requires_grad_())
    diag = torch.nn.Parameter(torch.randn((nbatch, nr)).to(dtype).requires_grad_())
    bias = torch.nn.Parameter(torch.zeros((nbatch, nr)).to(dtype).requires_grad_())
    y0 = torch.randn((nbatch, nr)).to(dtype)

    fwd_options = {**options, "method": method}
    model = DummyModule(A, addx=False)
    model.set_diag_bias(diag, bias)
    y = equilibrium(model.forward, y0, **fwd_options)
    f = model.forward(y)
    assert torch.allclose(y, f)

@device_dtype_float_test(only64=True, additional_kwargs={
    "method": ["broyden1", "broyden2", "linearmixing"]
})
def test_minimize_methods(dtype, device, method):
    torch.manual_seed(400)
    random.seed(100)

    nr = 3
    nbatch = 2
    default_fwd_options = {
        "max_niter": 50,
        "f_tol": 1e-9,
        "alpha": -1.0,
    }
    linearmixing_fwd_options = {
        "max_niter": 50,
        "f_tol": 3e-6,
        "alpha": -0.3,
    }
    # list the methods and the options here
    options = {
        "broyden1": default_fwd_options,
        "broyden2": default_fwd_options,
        "linearmixing": linearmixing_fwd_options,
    }[method]

    # specify higher atol for non-ideal method
    atol = defaultdict(lambda: 1e-8)
    atol["linearmixing"] = 3e-6

    A    = torch.nn.Parameter((torch.randn((nr, nr)) * 0.5).to(dtype).requires_grad_())
    diag = torch.nn.Parameter(torch.randn((nbatch, nr)).to(dtype).requires_grad_())
    # bias will be detached from the optimization line, so set it undifferentiable
    bias = torch.zeros((nbatch, nr)).to(dtype)
    y0 = torch.randn((nbatch, nr)).to(dtype)
    activation = "square"  # square activation makes it easy to optimize

    fwd_options = {**options, "method": method}
    model = DummyModule(A, addx=False, activation=activation, sumoutput=True)
    model.set_diag_bias(diag, bias)
    y = minimize(model.forward, y0, **fwd_options)

    # check the grad (must be close to 1)
    with torch.enable_grad():
        y1 = y.clone().requires_grad_()
        f = model.forward(y1)
    grady, = torch.autograd.grad(f, (y1,))
    assert torch.allclose(grady, grady * 0, atol=atol[method])

    # check the hessian (must be posdef)
    h = hess(model.forward, (y1,), idxs=0).fullmatrix()
    eigval, _ = torch.symeig(h)
    assert torch.all(eigval >= 0)
