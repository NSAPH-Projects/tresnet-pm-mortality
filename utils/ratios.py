import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.distributions as dists


def inverse_shift(treatment, delta, shift_type):
    assert shift_type in ("subtract", "percent")
    if shift_type == "subtract":
        t_delta = treatment + delta
        log_det = 0
    elif shift_type == "percent":
        t_delta = treatment / (1 - delta)
        log_det = -torch.log(1 - delta)
    else:
        raise NotImplementedError(shift_type)
    return t_delta, log_det


def shift(treatment, delta, shift_type):
    assert shift_type in ("subtract", "percent")
    if shift_type == "subtract":
        t_delta = treatment - delta
    elif shift_type == "percent":
        t_delta = treatment * (1 - delta)
    else:
        raise NotImplementedError(shift_type)
    return t_delta


def log_density_ratio_under_shift(
    treatment, delta, density_estimator, z, shift_type, eps=1e-10
):
    """z is the hidden vector for efficiency,
    eps just avoids nan on the log"""
    t_delta, log_det = inverse_shift(treatment, delta, shift_type)
    numer = density_estimator(t_delta, z)
    denom = density_estimator(treatment, z)
    log_ratio = torch.log(eps + numer)  - log_det - torch.log(eps + denom)

    return log_ratio


class ScaledRegularizer(nn.Module):
    def __init__(
        self,
        delta_list: torch.Tensor | None = None,
        multiscale: bool = True,
        fit_scale: bool = True,
    ) -> None:
        super().__init__()
        assert delta_list is not None, "provide delta list"
        self.register_buffer("multiscale", torch.tensor(multiscale))
        if multiscale:
            self.lsig = nn.Parameter(
                torch.zeros_like(delta_list), requires_grad=fit_scale
            )
        else:
            self.lsig = nn.Parameter(torch.tensor(0.0), requires_grad=fit_scale)
        self.register_buffer("delta_list", delta_list)

    def sig(self):
        return self.lsig.clamp(min=-10, max=10).exp()

    def prior(self):
        return -dists.HalfCauchy(1.0).log_prob(self.sig()).sum()

    def forward(self):
        raise NotImplementedError


class RatioRegularizer(ScaledRegularizer):
    def __init__(self, ls=0.01, **kwargs) -> None:
        super().__init__(**kwargs)
        self.ls = ls

    def forward(self, treatment, density_estimator, z, shift_type):
        # sample delta at random for each element
        ix = torch.randint(
            high=len(self.delta_list),
            size=(treatment.shape[0],),
            device=treatment.device,
        )
        delta = self.delta_list[ix]

        # make a pseudo transformed treatment
        shifted = shift(treatment, delta, shift_type)

        # obtain density ratio logits for shited and normal
        logits_shifted = log_density_ratio_under_shift(
            shifted, delta, density_estimator, z, shift_type
        )
        logits_unshifted = log_density_ratio_under_shift(
            treatment, delta, density_estimator, z, shift_type
        )

        # classification targets are 1 for shifted and 0 for unshifted
        logits = torch.cat([logits_shifted, logits_unshifted])
        tgts = torch.cat([torch.ones_like(treatment), torch.zeros_like(treatment)])
        tgts = tgts.clamp(self.ls, 1 - self.ls)

        # make loss and return
        sig = self.sig()[ix] if self.multiscale else self.sig()
        return (1.0 / sig**2) * F.binary_cross_entropy_with_logits(logits, tgts)


class VarianceRegularizer(ScaledRegularizer):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

    def forward(self, treatment, density_estimator, z, shift_type):
        # sample delta at random for each element
        ix = torch.randint(
            high=len(self.delta_list),
            size=(treatment.shape[0],),
            device=treatment.device,
        )
        delta = self.delta_list[ix]

        logits = log_density_ratio_under_shift(
            treatment, delta, density_estimator, z, shift_type
        )

        # minimizing the differnces between contiguous observations
        # is equivalent to variance minimization using u-statistics
        approx_variance = 0.5 * logits.diff().pow(2).mean()

        # make loss and return
        sig = self.sig()[ix] if self.multiscale else self.sig()
        return sig.pow(-2) * approx_variance


class PosteriorRegularizer(ScaledRegularizer):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

    def forward(self, treatment, model, covariates, shift_type):
        assert model.dropout > 0.0, "posterior variance regularizer only with dropout"

        # sample delta at random for each element
        ix = torch.randint(
            high=len(self.delta_list),
            size=(treatment.shape[0],),
            device=treatment.device,
        )
        delta = self.delta_list[ix]

        # draw two copies
        log_dr = []
        for _ in range(2):
            z = model(treatment, covariates)["z"]
            L = log_density_ratio_under_shift(
                treatment, delta, model.density_estimator, z, shift_type
            )
            log_dr.append(L)
        variance = 0.5 * (log_dr[1] - log_dr[0]).pow(2).mean()

        # make loss and return
        sig = self.sig()[ix] if self.multiscale else self.sig()
        return sig.pow(-2) * variance
