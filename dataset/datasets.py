import random
import os

import pandas as pd
import numpy as np

#import pickle

import torch
from torch.utils.data import TensorDataset, Dataset, DataLoader
from torch import cat, stack
from torch import Tensor
from tresnet.models import VCNet


from typing import Optional, Tuple, Dict

DATASETS = (
    "sim-N",  # simu1 simulated data in VCNet (Nie et al., 2021)
    "ihdp-N",  # IHDP modification in VCNet (Nie et al., 2021)
    "news-N",  # News modification in VCNet (Nie et al., 2021)
    "sim-B",  # Simulated data in SCIGAN (Bica et al., 2020)
    "news-B",  # News modification in SCIGAN (Bica et al., 2020)
    "tcga-B1",  # TCGA modification in SCIGAN (Bica et al., 2020)
    "tcga-B2",  # TCGA modification in SCIGAN (Bica et al., 2020)
    "tcga-B3",  # TCGA modification in SCIGAN (Bica et al., 2020)
    "sim-T",  # Simulated data in E2B (Taha Bahadori et al., 2022)
    "medisynth",  # FRrom fitting to the Medicare example
)


# class DatasetFromMatrix(Dataset):
#     """Create the pyTorch Dataset object that groes into the dataloader."""

#     def __init__(self, data_matrix):
#         """
#         Args: create a torch dataset from a tensor data_matrix with size n * p
#         [treatment, features, outcome]`z
#         """
#         self.data_matrix = data_matrix
#         self.num_data = data_matrix.shape[0]

#     def __len__(self):
#         return self.num_data

#     def __getitem__(self, idx: int) -> dict:
#         sample = self.data_matrix[idx, :]

#         return {
#             "treatment": sample[0],
#             "covariates": sample[1:-1],
#             "outcome": sample[-1],
#         }

def normalize_tcga_data(patient_features):
    x = (patient_features - np.min(patient_features, axis=0)) / (
        np.max(patient_features, axis=0) - np.min(patient_features, axis=0)
    )
    for i in range(x.shape[0]):
        x[i] = x[i] / np.linalg.norm(x[i])
    return x
        
def compute_tcga_beta(alpha, optimal_dosage):
    if optimal_dosage <= 0.001 or optimal_dosage >= 1.0:
        beta = 1.0
    else:
        beta = (alpha - 1.0) / float(optimal_dosage) + (2.0 - alpha)

    return beta

def get_iter(data_matrix, batch_size, **kwargs):
    # dataset = DatasetFromMatrix(data_matrix)
    treatment, covariates, outcome = (
        data_matrix[:, 0],
        data_matrix[:, 1:-1],
        data_matrix[:, -1],
    )
    dataset = TensorDataset(treatment, covariates, outcome)
    iterator = DataLoader(dataset, batch_size=batch_size, **kwargs)
    return iterator


def set_seed(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.enabled = False


def load_data(
    dataset: str,
    n_train: Optional[int] = None,
    n_test: Optional[int] = None,
    noise_scale: float = 0.5,
) -> Tuple[int]:
    """n_train, n_test only useful for simulated datasets"""
    if dataset == "sim-N":  # simu1 simulated data in VCNet (Nie et al., 2021)
        assert n_train is not None, "n_train cannot be None for simulated data"
        n = n_train + n_test
        x = torch.rand((n, 6))
        x1, x2, x3, x4, x5 = [x[:, j] for j in range(5)]
        logits = (
            (10.0 * Max(x1, x2, x3).sin() + Max(x3, x4, x5).pow(3))
            / (1.0 + (x1 + x5).pow(2))
            + (0.5 * x3).sin() * (1.0 + (x4 - 0.5 * x3).exp())
            + x3.pow(2)
            + 2.0 * x4.sin()
            + 2 * x5
            - 6.5
        )
        t = (logits + noise_scale * torch.randn(n)).sigmoid()
        train_ix = torch.arange(0, n_train)
        test_ix = torch.arange(n_train, n_train + n_test)
        D = {"x": x, "t": t, "train_ix": train_ix, "test_ix": test_ix}
        return D

    elif dataset == "sim-B":
        n_confounders = 5
        n_samples = 1000

        diag = np.ones((n_confounders))
        off_diag = np.full((n_confounders - 1), fill_value=0.2)  # [0]

        # Create cov matrix
        cov_matrix = np.zeros((n_confounders, n_confounders))

        # Make the matrix tridiagonal
        tridiagonal_matrix = (
            cov_matrix + np.diag(diag, 0) + np.diag(off_diag, -1) + np.diag(off_diag, 1)
        )

        # x is the covariates, there are 5 covariates per sample
        x = np.random.multivariate_normal(
            mean=np.zeros((n_confounders,)), cov=tridiagonal_matrix, size=n_samples
        )
        x = torch.FloatTensor(x)

        beta = torch.FloatTensor(np.random.uniform(low=-1, high=1, size=n_confounders))
        mu_t = np.sin(x @ beta)

        # t is the treatment
        t = torch.sigmoid(mu_t + 0.3 * torch.randn(n_samples))

        ix_list = torch.randperm(n_samples)
        train_ix = ix_list[:800]
        test_ix = ix_list[800:]
        D = {
            "x": torch.FloatTensor(x),
            "t": torch.FloatTensor(t),
            "train_ix": train_ix,
            "test_ix": test_ix,
        }
        return D

    elif dataset == "ihdp-N":  # IHDP modification in VCNet (Nie et al., 2021)
        if not os.path.exists("dataset/ihdp/ihdp.csv"):
            raise FileNotFoundError("The dataset path does not exist")

        x = pd.read_csv("dataset/ihdp/ihdp.csv", usecols=range(2, 27))
        x = torch.FloatTensor(x.to_numpy())
        n = x.shape[0]

        # normalize the data
        # !mauricio: really weird normalization
        for _ in range(x.shape[1]):
            minval = (x[:, _]).min()
            maxval = (x[:, _]).max()
            x[:, _] = (x[:, _] - minval) / maxval
        # x = (x - x.amin(0)) / x.amax(0)

        cate_idx1 = torch.tensor([3, 6, 7, 8, 9, 10, 11, 12, 13, 14])
        cate_idx2 = torch.tensor([15, 16, 17, 18, 19, 20, 21, 22, 23, 24])

        x1, x2, x3, x4, x5 = [x[:, j] for j in [0, 1, 2, 4, 5]]
        logits = (
            x1 / (1.0 + x2)
            + Max(x3, x4, x5) / (0.2 + Min(x3, x4, x5))
            + ((x[:, cate_idx2].mean(1) - x[:, cate_idx1].mean()) * 5.0).tanh()
            - 2.0
        )
        t = (logits + noise_scale * torch.randn(n)).sigmoid()

        #! Dimeji: should we be randomly permuting the indexes before then selecting training and test?
        # train_ix = torch.arange(0, 473)
        # test_ix = torch.arange(473, len(t))
        #! Mauricio: ok
        ix_list = torch.randperm(n)
        train_ix = ix_list[:473]
        test_ix = ix_list[473:]
        D = {"x": x, "t": t, "train_ix": train_ix, "test_ix": test_ix}
        return D

    elif dataset == "news-N":  # News modification in VCNet (Niet et al., 2021)
        # Load preprocessed data from numpy file
        news = np.load("dataset/news/news_preprocessed.npy")
        news = torch.FloatTensor(news)

        # Normalize the data
        news = news / news.amax(0)

        # Get shape variables
        n_samples = news.shape[0]
        n_features = news.shape[1]

        #! Dimeji: We have to optimize this.
        #! Dimeji: Currently we are running this pseudorandom number generator here and in the outcome function
        # np.random.seed(5)
        # v1_stack = torch.FloatTensor(
        #     [v1 / np.sqrt(np.sum(v1**2)) for _ in range(n_samples)]
        # )
        # v2 = np.random.randn(n_features)
        # v2_stack = torch.FloatTensor(
        #     [v2 / np.sqrt(np.sum(v2**2)) for _ in range(n_samples)]
        # )
        # v3 = np.random.randn(n_features)
        # v3_stack = torch.FloatTensor(
        #     [v3 / np.sqrt(np.sum(v3**2)) for _ in range(n_samples)]
        # )

        #! Mauricio: commented the random seed and vectorized the generation code
        V = torch.randn((3, n_samples, n_features))
        V = V / V.norm(p=2, dim=-1, keepdim=True)

        alpha = 1 / noise_scale
        tt = 0.5 * torch.mul(V[1], news).sum(1) / torch.mul(V[2], news).sum(1)

        betas = (alpha - 1) / tt + 2 - alpha
        betas = np.abs(betas) + 0.0001
        # treatment = torch.FloatTensor(
        #     [np.random.beta(alpha, beta, 1)[0] for beta in betas]
        # )
        #! Mauricio: direct sampling with torch
        treatment = torch.distributions.Beta(alpha, betas).sample()

        #! Dimeji: A random permutation has been applied here to the indxes of the data
        idx_list = torch.randperm(n_samples)
        train_ix = idx_list[0:2000]
        test_ix = idx_list[2000:]

        D = {
            "x": news,
            "t": treatment,
            "train_ix": train_ix,
            "test_ix": test_ix,
            "V": V,
        }

        # Load Neural

        return D

    elif dataset in set(["tcga-B1", "tcga-B2", "tcga-B3"]):

        with open("dataset/tcga/tcga.p", "rb") as f:
            import pickle

            tcga_data = pickle.load(f)

        patients = normalize_tcga_data(tcga_data["rnaseq"])

        # 9659 patients with 4000 features describing them each.

        num_weights = 3

        V = np.random.normal(loc=0.0, scale=1.0, size=(num_weights, patients.shape[1]))

        for col in range(V.shape[1]):
            V[:, col] = V[:, col] / np.linalg.norm(V[:, col], ord=1)


        idx_list = torch.randperm(patients.shape[0])
        train_ix = idx_list[0 : int(len(idx_list) * 0.8)] 
        test_ix = idx_list[int(len(idx_list) * 0.8) :]

        if dataset == "tcga-B1":  # TCGA modification in SCIGAN (Bica et al., 2020)
            # Utility functions


            def generate_dosage_treatment(
                x,
                v,
                dosage_selection_bias=2,
                scaling_parameter=10,
            ):

                # Treatment 1

                b = 0.75 * np.dot(x, v[1]) / (np.dot(x, v[2]))

                dosage_selection_bias = 2
                optimal_dosage = np.dot(x, v[1]) / (2.0 * np.dot(x, v[2]))
                alpha = dosage_selection_bias
                dosage = np.array([np.random.beta(alpha, compute_tcga_beta(alpha, elem)) for elem in optimal_dosage])
                dosage = np.array([1 - d if o <= 0.001 else d for (d, o) in zip(dosage, optimal_dosage)])
                return dosage

            # Create load data and create treatment


            dosages = generate_dosage_treatment(patients, V)  # generate dosages


            
            D = {
                "x": torch.tensor(patients, dtype=torch.float32),
                "t": torch.tensor(dosages, dtype=torch.float32),
                "train_ix": train_ix,
                "test_ix": test_ix,
                "V": V,
            }

            # D = {
            #    "x": torch.FloatTensor(tcga_data["x"]),
            #    "t": torch.FloatTensor(tcga_data["t"]),
            #    "train_ix": torch.LongTensor(tcga_data["train_idx"]),
            #    "test_ix": torch.LongTensor(tcga_data["test_idx"]),
            #    "y": torch.FloatTensor(tcga_data["y"]),
            # }
            return D

        elif dataset == "tcga-B2":

            def generate_dosage_treatment(
                x,
                v,
                dosage_selection_bias=2,
                scaling_parameter=10,
                ):

                optimal_dosage = np.dot(x, v[1]) / (2.0 * np.dot(x, v[2]))
                alpha = dosage_selection_bias
                #dosage = np.random.beta(alpha, compute_beta(alpha, optimal_dosage))
                dosage = np.array([np.random.beta(alpha, compute_tcga_beta(alpha, elem)) for elem in optimal_dosage])
                dosage = np.array([1 - d if o <= 0.001 else d for (d, o) in zip(dosage, optimal_dosage)])
                
                return dosage 
            
            dosages = generate_dosage_treatment(patients, V)

            D = {
                "x": torch.tensor(patients, dtype=torch.float32),
                "t": torch.tensor(dosages, dtype=torch.float32),
                "train_ix": train_ix,
                "test_ix": test_ix,
                "V": V,
            }
            return D
            
        elif dataset == "tcga-B3":

            def generate_dosage_treatment(
                x,
                v,
                dosage_selection_bias=2,
                scaling_parameter=10,
            ):

                # Treatment 3

                b = 0.75 * np.dot(x, v[1]) / (np.dot(x, v[2]))

                optimal_dosage = np.array(
                    [elem / 3.0 if elem >= 0.75 else 1.0 for elem in b]
                )

                alpha = dosage_selection_bias

                dosage = np.array(
                    [
                        np.random.beta(alpha, compute_tcga_beta(alpha, elem))
                        for elem in optimal_dosage
                    ]
                )
                return dosage

            dosages = generate_dosage_treatment(patients, V)

            D = {
                "x": torch.tensor(patients, dtype=torch.float32),
                "t": torch.tensor(dosages, dtype=torch.float32),
                "train_ix": train_ix,
                "test_ix": test_ix,
                "V": V,
            }

            # D = {
            #    "x": torch.FloatTensor(tcga_data["x"]),
            #    "t": torch.FloatTensor(tcga_data["t"]),
            #    "train_ix": torch.LongTensor(tcga_data["train_idx"]),
            #    "test_ix": torch.LongTensor(tcga_data["test_idx"]),
            #    "y": torch.FloatTensor(tcga_data["y"]),
            # }
            return D
            

        


    elif dataset == "news-B":  # News modification in SCIGAN (Bica et al., 2020)
        raise NotImplementedError

    elif dataset == "sim-T":  # Simulated data in E2B (Taha Bahadori et al., 2022)
        raise NotImplementedError
    elif dataset == "medisynth":
        import pickle

        with open("dataset/medisynth/medisynth.pkl", "rb") as f:
            data = pickle.load(f)
        # make train and test split indices 80/20 splits
        n = len(data["treatment"])
        train_idx = np.random.choice(n, int(0.8 * n), replace=False)
        test_idx = np.setdiff1d(np.arange(n), train_idx)

        #   must be identical to synthetiza_medicare.py
        density_estimator_config = [(data["covariates"].shape[1], 50, 1), (50, 50, 1)]
        pred_head_config = [(50, 50, 1), (50, 1, 1)]
        model = VCNet(
            density_estimator_config,
            num_grids=30,
            pred_head_config=pred_head_config,
            spline_degree=2,
            spline_knots=[0.33, 0.66],
            dropout=0.0,
        )
        # load best model
        model.load_state_dict(torch.load("dataset/medisynth/medisynth.pth"))
        model.eval()
        # get prediction
        t = data["treatment"]
        x = data["covariates"]
        with torch.no_grad():
            q = model(t, x)["predicted_outcome"]
        qmin = float(q.min())
        qmax = float(q.max())

        D = {
            "x": torch.FloatTensor(data["covariates"]),
            "t": torch.FloatTensor(data["treatment"]),
            "train_ix": torch.LongTensor(train_idx),
            "test_ix": torch.LongTensor(test_idx),
            "qmin": qmin,
            "qmax": qmax,
        }
        return D
    else:
        raise ValueError(dataset)


def support(dataset: str) -> str:
    """Returns link and inverse link"""
    if dataset in (
        "sim-N",
        "ihdp-N",
        "news-N",
        "sim-B",
        "medisynth",
    ):  # VCNet datasets (Nie et al., 2021)
        return "unit"
    else:
        return "real"


def outcome(
    D: dict,
    dataset: str,
    noise: Tensor | None = None,
    treatment: Tensor | None = None,
    noise_scale: float = 0.5,
) -> Tensor:
    x = D["x"]
    t = D["t"] if treatment is None else treatment
    if dataset == "sim-N":  # simu1 simulated data in VCNet (Nie et al., 2021)
        x1, x3, x4, x6 = [x[:, j] for j in [0, 2, 3, 5]]
        mu = ((t - 0.5) * 2 * torch.pi).cos() * (
            t**2 + (4 * Max(x1, x6).pow(3)) / (1.0 + 2 * x3.pow(2)) * x4.sin()
        )
        if noise is None:
            noise = noise_scale * torch.randn_like(t)
        y = mu + noise
        return y, noise

    elif dataset == "sim-B":
        if noise is not None:
            beta, gams, error = noise
        else:
            beta = torch.randn(5)
            gams = torch.randn(4)
            error = noise_scale * torch.randn_like(t)

        def hermit_polynomial(treatment, gams):
            # gamma_0, gamma_1, gamma_2, gamma_3 = np.random.normal(size=4)
            return (
                gams[0]
                + (gams[1] * treatment)
                + (gams[2] * (treatment**2 - 1))
                + (gams[3] * (treatment**3 - (3 * treatment)))
            )

        # beta_x = x @ beta
        # beta_x_norm = beta_x / np.linalg.norm(beta_x, ord=2)
        # h(a) = gam[0] + gam[1] * a + gam[2] * (a**2 - 1) + gam[3] * (a***3 - 3 * a)
        hermit = hermit_polynomial(torch.logit(t), gams) + x @ beta

        y = hermit + error

        #! Dimeji:  I am assuming we don't need any noise in this case, so I set noise to None
        #! Mauricio: treated same as others
        noise = (beta, gams, error)
        return y, noise

    elif dataset == "ihdp-N":  # IHDP modification in VCNet (Niet et al., 2021)
        x1, x2, x3, x4, x5 = [x[:, j] for j in [0, 1, 2, 4, 5]]
        factor1, factor2 = 1.5, 0.5
        cate_idx1 = torch.tensor([3, 6, 7, 8, 9, 10, 11, 12, 13, 14])
        mu = (
            1.0
            / (1.2 - t)
            * torch.sin(t * 3.0 * torch.pi)
            * (
                factor1
                * torch.tanh((x[:, cate_idx1].mean(1) - x[:, cate_idx1].mean()) * 5.0)
                + factor2 * torch.exp(0.2 * (x1 - x5)) / (0.1 + Min(x2, x3, x4))
            )
        )
        if noise is None:
            noise = noise_scale * torch.randn_like(t)
        y = mu + noise
        return y, noise
    elif dataset == "news-N":  # News modification in VCNet (Niet et al., 2021)
        V = D["V"]
        news = x

        A = ((torch.mul(V[1], news)).sum(1)) / ((torch.mul(V[2], news)).sum(1))
        res1 = torch.clamp(torch.exp(0.3 * torch.pi * A - 1), min=-2, max=2)
        res2 = 20.0 * ((torch.mul(V[0], news)).sum(1))
        res = 2 * (4 * (t - 0.5) ** 2 * np.sin(0.5 * torch.pi * t)) * (res1 + res2)

        if noise is None:
            noise = noise_scale * torch.randn_like(t)
        y = res + noise
        return y, noise

    elif dataset == "tcga-B1":  # TCGA modification in SCIGAN (Bica et al., 2020)
        V = D["V"]
        x = x.numpy()
        t = t.numpy()

        y = 10 * (np.dot(V[0], x.T) + (np.dot((12.0 * V[1]), x.T) *  t) - (np.dot((12.0 * V[2]), x.T) *  (t ** 2)))

        noise = np.random.normal(0, 0.2, size = len(y))
        y = y + noise
        return torch.tensor(y, dtype=torch.float32), torch.tensor(noise, dtype=torch.float32)
    
    elif dataset == "tcga-B2":

        V = D["V"]
        x = x.numpy()
        t = t.numpy()

        y = 10.0 * ((np.dot(x, V[0])) + (np.sin(np.pi * (np.dot(x, V[1]) / np.dot(x, V[2])) * t)))

        noise = np.random.normal(0, 0.2, size = len(y))
        y = y + noise
        return torch.tensor(y, dtype=torch.float32), torch.tensor(noise, dtype=torch.float32)

    elif dataset == "tcga-B3":

        V = D["V"]
        x = x.numpy()
        t = t.numpy()

        y = 10.0 * ((np.dot(x, V[0])) + (12.0 * t * ((t - (0.75 * (np.dot(x, V[1]) / np.dot(x, V[2]))) ** 2))))

        noise = np.random.normal(0, 0.2, size = len(y))
        y = y + noise
        return torch.tensor(y, dtype=torch.float32), torch.tensor(noise, dtype=torch.float32)

    elif dataset == "news-B":  # News modification in SCIGAN (Bica et al., 2020)
        raise NotImplementedError
    elif dataset == "sim-T":  # Simulated data in E2B (Taha Bahadori et al., 2022)
        raise NotImplementedError
    elif dataset == "medisynth":
        # make neural network model
        density_estimator_config = [(D["x"].shape[1], 50, 1), (50, 50, 1)]
        pred_head_config = [(50, 50, 1), (50, 1, 1)]

        # must be identical to synthetiza_medicare.py
        model = VCNet(
            density_estimator_config,
            num_grids=30,
            pred_head_config=pred_head_config,
            spline_degree=2,
            spline_knots=[0.33, 0.66],
            dropout=0.0,
        )
        # load best model
        model.load_state_dict(torch.load("dataset/medisynth/medisynth.pth"))
        model.eval()
        # get prediction
        with torch.no_grad():
            q = model(t, x)["predicted_outcome"]
            q = (q - D["qmin"]) / (D["qmax"] - D["qmin"])

        if noise is None:
            noise = noise_scale * torch.randn_like(t)
        y = q + noise
        return y, noise

    else:
        raise ValueError(dataset)


def make_dataset(
    dataset: str,
    delta_list: Tensor,
    noise_scale: float = 0.5,
    count: bool = False,
    **kwargs,
) -> Dict:
    """
    delta_std is the number of standard deviations to reduce from the treatment
    n_train, n_test only useful for simulated datasets
    akwargs are passed to load_data
    """
    # -- should be same as vcnet code, but vectorized -- #

    D = load_data(dataset, noise_scale=noise_scale, **kwargs)
    x, t, train_ix, test_ix = D["x"], D["t"], D["train_ix"], D["test_ix"]
    y, noise = outcome(D, dataset, noise_scale=noise_scale)

    if count:
        scale = y.max()
        y = (2.0 * y / scale).exp().round()
        # r = 5.0
        # m = y.mean()
        # y2 = (y - m) ** 2
        # a, b = y2.min(), y2.max()
        # y2_unit = (y2 - a) / (b - a)
        # y = (r * y2_unit).exp().round()

    train_matrix = cat([t[train_ix, None], x[train_ix], y[train_ix, None]], dim=1)
    test_matrix = cat([t[test_ix, None], x[test_ix], y[test_ix, None]], dim=1)

    # -- specific to stochastic interventions -- #
    supp = support(dataset)

    if supp == "unit":  # treatment in (0,1)
        delta_scale = None
        shifted_t = [t * float(1 - d) for d in delta_list]
        shift_type = "percent"
        t_grid = torch.linspace(0, 1, 100)
    elif supp == "real":  # treatment in real line
        delta_scale = t.std()
        shifted_t = [t - delta_scale * d for d in delta_list]
        shift_type = "subtract"
        t_grid = torch.linspace(t.min(), t.max(), 100)
    else:
        raise NotImplementedError

    # make counterfactuals and shift-response functions
    cfs = stack(
        [outcome(D, dataset, treatment=tcf, noise=noise)[0] for tcf in shifted_t], 1
    )

    if count:
        cfs = (2.0 * cfs / scale).exp().round()
        # cfs2 = (cfs - m) ** 2
        # cfs2_unit = (cfs2 - a) / (b - a)
        # cfs = (r * cfs2_unit).exp().round()

    # average the counterfactuals for value of delta
    srf_train = cfs[train_ix, :].mean(0)
    srf_test = cfs[test_ix, :].mean(0)

    # similar as above, compute the exposure response function for t_grid
    cfs_erf = stack(
        [
            outcome(D, dataset, treatment=torch.full_like(t, tcf), noise=noise)[0]
            for tcf in t_grid
        ],
        axis=1,
    )
    erf_train = cfs_erf[train_ix, :].mean(0)
    erf_test = cfs_erf[test_ix, :].mean(0)

    return {
        "train_matrix": train_matrix,
        "test_matrix": test_matrix,
        "srf_train": srf_train,
        "srf_test": srf_test,
        "delta_scale": delta_scale,
        "shift_type": shift_type,
        "t_grid": t_grid,
        "erf_train": erf_train,
        "erf_test": erf_test,
    }


def Max(*args):
    """point wise max of tensors"""
    return stack(list(args), dim=1).amax(dim=1)


def Min(*args):
    """point wise min of tensors"""
    return stack(list(args), dim=1).amin(dim=1)
