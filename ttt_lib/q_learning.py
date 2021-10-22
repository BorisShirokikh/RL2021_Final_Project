import random

import numpy as np
from tqdm import trange
import torch
import torch.nn as nn
from torch.optim import SGD
from torch.nn.functional import mse_loss
from dpipe.torch import to_device, to_var
from dpipe.layers import ResBlock2d

from ttt_lib.utils import augm_spatial
from ttt_lib.torch import optimizer_step, Bias, MaskedSoftmax


class PolicyNetworkQ(nn.Module):
    def __init__(self, structure=None, n=3, in_channels=5):
        super().__init__()
        if structure is None:
            structure = (128, 64)
        self.structure = structure
        self.n = n
        self.in_channels = in_channels

        n_features = structure[0]
        n_features_policy = structure[1]
        self.model = nn.Sequential(
            nn.Conv2d(in_channels, n_features, kernel_size=3, padding=1, bias=False),
            nn.ReLU(inplace=True),
            ResBlock2d(n_features, n_features, kernel_size=3, padding=1, batch_norm_module=nn.Identity),
            # policy head
            nn.Conv2d(n_features, n_features_policy, kernel_size=1, padding=0, bias=False),
            nn.ReLU(inplace=True),
            nn.Conv2d(n_features_policy, 1, kernel_size=1, padding=0, bias=False),
            nn.Flatten(),
            Bias(n_channels=n ** 2),
            nn.Tanh(),
        )
        self.flatten = nn.Flatten()
        self.masked_softmax = MaskedSoftmax(dim=1)

    def forward(self, x):
        return torch.reshape(self.model(x), shape=(-1, 1, self.n, self.n))

    def predict_proba(self, x, q):
        p = self.masked_softmax(self.flatten(q), self.flatten(x[:, -1, ...].unsqueeze(1)))
        return torch.reshape(p, shape=(-1, 1, self.n, self.n))


class PolicyPlayer:
    def __init__(self, model, field, eps=0.0, device='cpu', model_opp=None):
        self.model = to_device(model, device=device)
        self.field = field

        self.eps = eps
        self.device = device

        self.model_opp = model_opp if model_opp is None else to_device(model_opp, device=device)

        # fields to slightly speedup computation:
        self.n = self.field.get_size()

        self.eval()

    def _forward_action(self, i, j):
        self.field.make_move(i, j)
        return self.field.get_value()

    def _calc_move(self, policy, eps):
        avail_actions = torch.nonzero(self.field.get_running_features()[:, -1, ...].reshape(-1)).reshape(-1)
        n_actions = len(avail_actions)

        avail_p = policy.reshape(-1)[avail_actions]
        argmax_avail_action_idx = random.choice(torch.where(avail_p == avail_p.max(), 1., 0.).nonzero()).item()

        if eps > 0:
            soft_p = np.zeros(n_actions) + eps / n_actions
            soft_p[argmax_avail_action_idx] += (1 - eps)
            argmax_avail_action_idx = np.random.choice(np.arange(n_actions), p=soft_p)

        action = avail_actions[argmax_avail_action_idx].item()

        return action, action // self.n, action % self.n

    def eval(self):
        self.model.eval()

    def train(self):
        self.model.train()

    def forward(self, x):
        return self.model(x)

    def forward_state(self, state=None):
        state = self.field.get_running_features() if state is None else self.field.field2features(field=state)
        policy = self.forward(state)
        proba = self.model.predict_proba(state, policy)
        return policy[0][0], proba[0][0]

    def action(self, train=True):
        eps = self.eps if train else 0

        policy, proba = self.forward_state()

        action, i, j = self._calc_move(policy=policy, eps=eps)
        value = self._forward_action(i, j)

        return policy, proba, action, value

    def manual_action(self, i, j):
        # compatibility with `action` output:
        action = i * self.n + j
        policy_manual = to_var(np.zeros((self.n, self.n), dtype='float32'), device=self.device)
        policy_manual[i, j] = 1
        proba_manual = to_var(np.zeros((self.n, self.n), dtype='float32'), device=self.device)
        proba_manual[i, j] = 1

        value = self._forward_action(i, j)

        return policy_manual, proba_manual, action, value

    def update_field(self, field):
        self.field.set_state(field=field)


def play_game(player, field=None, train=True, augm=False):
    if field is None:
        field = np.zeros((player.field.get_size(), ) * 2, dtype='float32')
    player.update_field(field=field)

    if not train:
        augm = False

    s_history = []      # states
    f_history = []      # features
    a_history = []      # actions
    q_history = []      # policies
    q_max_history = []  # max Q values
    p_history = []      # probas

    player.eval()
    v = None  # v -- value
    while v is None:
        s_history.append(player.field.get_state())
        f_history.append(player.field.get_features())
        q, p, a, v = player.action(train=train)
        q_history.append(q)
        q_max_history.append(q[p > 0].max().item())
        p_history.append(p)
        a_history.append(a)

        if augm:
            player.update_field(augm_spatial(player.field.get_state()))

    return s_history, f_history, a_history, q_history, q_max_history, p_history, v


def train_q_learning(player, n_episodes=1000, n_step_q=1, ep2eps=None, lr=1e-2, weight_decay=1e-4, augm=False,
                     logger=None):
    if n_step_q not in (1, 2):
        raise ValueError(f'`n_step_q` should be 1 or 2; however, {n_step_q} is given.')

    n = player.field.get_size()
    optimizer = SGD(player.model.parameters(), lr=lr, momentum=0.9, weight_decay=weight_decay, nesterov=True)
    criterion = mse_loss

    loss_history = []
    ep2eps_curr_idx = None if ep2eps is None else 0
    ep2eps_items = None if ep2eps is None else list(ep2eps.items())
    for ep in trange(n_episodes):

        s_history, f_history, a_history, q_history, q_max_history, p_history, value\
            = play_game(player=player, augm=augm)

        player.train()
        qs_pred = player.forward(to_var(np.concatenate(f_history, axis=0), device=player.device)).squeeze(1)

        # TODO: select only acted values for backprob. (E.g., loss could be hardcoded.)
        qs_true = []
        rev_a_history = a_history[::-1]
        rev_q_history = torch.flip(qs_pred, dims=(0, ))
        rev_q_max_history = q_max_history[::-1]
        for t_rev, (a, q) in enumerate(zip(rev_a_history, rev_q_history)):
            q_true = q.clone().detach()  # TODO: check copy
            if n_step_q == 1:
                if t_rev == 0:
                    q_true[a // n, a % n] = value
                else:
                    q_true[a // n, a % n] = - rev_q_max_history[t_rev - 1]
            else:  # n_step_q == 2:
                if t_rev == 0:
                    q_true[a // n, a % n] = value
                elif t_rev == 1:
                    q_true[a // n, a % n] = - value
                else:
                    q_true[a // n, a % n] = rev_q_max_history[t_rev - 2]
            qs_true.append(q_true[None])

        qs_true = to_device(torch.cat(qs_true[::-1], dim=0), device=player.device)

        loss = criterion(qs_pred, qs_true)
        optimizer_step(optimizer=optimizer, loss=loss)

        if logger is not None:
            logger.add_scalar('loss/train', loss.item(), ep)

        loss_history.append(loss.item())

        # eps scheduler
        if ep2eps_curr_idx is not None:
            if ep == ep2eps_items[ep2eps_curr_idx][0]:
                player.eps = ep2eps_items[ep2eps_curr_idx][1]

    return loss_history
