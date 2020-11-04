from AgentRun import *
from AgentNet import *
from AgentZoo import *

"""
PPO
ceta2 ReacherBulletEnv, args.net_dim = 2 ** 7
ceta3 ReacherBulletEnv, args.net_dim = 2 ** 8

FixISAC, cancel actor_term
beta2 Ant
beta2 BW
beta2 LL

FixISAC, cancel actor_term, layer_lr
"""


class InterSPG1101(nn.Module):  # class AgentIntelAC for SAC (SPG means stochastic policy gradient)
    def __init__(self, state_dim, action_dim, mid_dim):
        self.c = 0

        super().__init__()
        self.log_std_min = -20
        self.log_std_max = 2
        self.constant_log_sqrt_2pi = np.log(np.sqrt(2 * np.pi))
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # encoder
        self.enc_s = nn.Sequential(
            nn.Linear(state_dim, mid_dim), nn.ReLU(),
            nn.Linear(mid_dim, mid_dim),
        )  # state
        self.enc_a = nn.Sequential(
            nn.Linear(action_dim, mid_dim), nn.ReLU(),
            nn.Linear(mid_dim, mid_dim),
        )  # action without nn.Tanh()

        self.net = DenseNet(mid_dim)
        net_out_dim = self.net.out_dim

        # decoder
        self.dec_a = nn.Sequential(
            nn.Linear(net_out_dim, mid_dim), nn.ReLU(),
            nn.Linear(mid_dim, action_dim),
        )  # action_mean
        self.dec_d = nn.Sequential(
            nn.Linear(net_out_dim, mid_dim), nn.ReLU(),
            nn.Linear(mid_dim, action_dim),
        )  # action_std_log (d means standard dev.)
        self.dec_q1 = nn.Sequential(
            nn.Linear(net_out_dim, mid_dim), nn.ReLU(),
            nn.Linear(mid_dim, 1),
        )  # q_value1 SharedTwinCritic
        self.dec_q2 = nn.Sequential(
            nn.Linear(net_out_dim, mid_dim), nn.ReLU(),
            nn.Linear(mid_dim, 1),
        )  # q_value2 SharedTwinCritic

        layer_norm(self.dec_a[-1], std=0.01)  # net[-1] is output layer for action, it is no necessary.
        layer_norm(self.dec_q1[-1], std=0.1)
        layer_norm(self.dec_q2[-1], std=0.1)

    def forward(self, s, noise_std=0.0):  # actor, in fact, noise_std is a boolean
        s_ = self.enc_s(s)
        a_ = self.net(s_)
        a_mean = self.dec_a(a_)  # NOTICE! it is a_mean without tensor.tanh()

        if noise_std != 0.0:
            a_std_log = self.dec_d(a_).clamp(self.log_std_min, self.log_std_max)
            a_std = a_std_log.exp()
            a_mean = torch.normal(a_mean, a_std)  # NOTICE! it is a_mean without .tanh()
        return a_mean.tanh()

    def get__a__log_prob_1101(self, state):  # actor
        s_ = self.enc_s(state)
        a_ = self.net(s_)
        a_mean = self.dec_a(a_)  # NOTICE! it is a_mean without .tanh()
        a_std_log = self.dec_d(a_).clamp(self.log_std_min, self.log_std_max)
        a_std = a_std_log.exp()

        """add noise to action, stochastic policy"""
        # a_noise = torch.normal(a_mean, a_std, requires_grad=True)
        # the above is not same as below, because it needs gradient
        # noise = torch.randn_like(a_mean, requires_grad=True, device=self.device)
        # a_noise = a_mean + a_std * noise

        # '''compute log_prob according to mean and std of action (stochastic policy)'''
        # a_delta = ((a_noise - a_mean) / a_std).pow(2) * 0.5
        # log_prob_noise = a_delta + a_std_log + self.constant_log_sqrt_2pi
        #
        # a_noise_tanh = a_noise.tanh()
        # log_prob = log_prob_noise + (-a_noise_tanh.pow(2) + 1.00001).log()

        noise = torch.randn_like(a_mean, requires_grad=True)
        a_noise = a_mean + a_std * noise

        a_noise_tanh = a_noise.tanh()
        fix_term = (-a_noise_tanh.pow(2) + 1.000001).log()
        # log_prob = a_delta - a_std_log.abs() + fix_term # todo Minitaur 18 # / 8,  Minitaur 24
        # log_prob = noise.pow(2) * 0.5 + a_std_log + fix_term

        p = ((-a_mean + 8).abs() / (noise.abs() + 0.01)).detach().log()
        log_prob = noise.pow(2) * 0.5 - (a_std_log - p).abs() + p + fix_term
        return a_noise_tanh, log_prob.sum(1, keepdim=True)

    def get__q1_q2(self, s, a):  # critic
        s_ = self.enc_s(s)
        a_ = self.enc_a(a)
        q_ = self.net(s_ + a_)
        q1 = self.dec_q1(q_)
        q2 = self.dec_q2(q_)
        return q1, q2


class AgentInterSAC1101(AgentBasicAC):  # Integrated Soft Actor-Critic Methods
    def __init__(self, state_dim, action_dim, net_dim):
        super(AgentBasicAC, self).__init__()
        self.learning_rate = 4e-4
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        self.act = InterSPG1101(state_dim, action_dim, net_dim).to(self.device)
        self.act.train()
        # self.act_optimizer = torch.optim.Adam(self.act.parameters(), lr=self.learning_rate)
        self.act_optimizer = torch.optim.Adam(
            [{'params': self.act.enc_s.parameters(), 'lr': self.learning_rate / 2},  # more stable
             {'params': self.act.enc_a.parameters(), },
             {'params': self.act.net.parameters(), 'lr': self.learning_rate / 2},
             {'params': self.act.dec_a.parameters(), },
             {'params': self.act.dec_d.parameters(), },
             {'params': self.act.dec_q1.parameters(), },
             {'params': self.act.dec_q2.parameters(), }, ]
            , lr=self.learning_rate)

        self.act_target = InterSPG1101(state_dim, action_dim, net_dim).to(self.device)
        self.act_target.eval()
        self.act_target.load_state_dict(self.act.state_dict())

        self.criterion = nn.SmoothL1Loss()

        '''training record'''
        self.state = None  # env.reset()
        self.reward_sum = 0.0
        self.step = 0
        self.update_counter = 0

        '''extension: auto-alpha for maximum entropy'''
        self.target_entropy = np.log(action_dim) - action_dim * np.log(np.sqrt(2 * np.pi))  # todo
        self.log_alpha = torch.tensor((-self.target_entropy,), requires_grad=True, device=self.device)
        self.alpha_optimizer = torch.optim.Adam((self.log_alpha,), lr=self.learning_rate)

        '''extension: reliable lambda for auto-learning-rate'''
        self.avg_loss_c = (-np.log(0.5)) ** 0.5

        '''constant'''
        self.explore_noise = True  # stochastic policy choose noise_std by itself.

    def update_policy(self, buffer, max_step, batch_size, repeat_times):
        self.act.train()

        log_prob = critic_loss = None  # just for print

        k = 1.0 + buffer.now_len / buffer.max_len
        batch_size = int(batch_size * k)  # increase batch_size
        train_step = int(max_step * k)  # increase training_step

        alpha = self.log_alpha.exp().detach()  # auto temperature parameter

        update_a = 0
        for update_c in range(1, train_step):
            with torch.no_grad():
                reward, mask, state, action, next_s = buffer.random_sample(batch_size, self.device)

                next_a_noise, next_log_prob = self.act_target.get__a__log_prob_1101(next_s)
                next_q_target = torch.min(*self.act_target.get__q1_q2(next_s, next_a_noise))  # twin critic
                q_target = reward + mask * (next_q_target + next_log_prob * alpha)  # # auto temperature parameter

            '''critic_loss'''
            q1_value, q2_value = self.act.get__q1_q2(state, action)  # CriticTwin
            critic_loss = self.criterion(q1_value, q_target) + self.criterion(q2_value, q_target)

            '''auto reliable lambda'''
            self.avg_loss_c = 0.995 * self.avg_loss_c + 0.005 * critic_loss.item() / 2  # soft update, twin critics
            lamb = np.exp(-self.avg_loss_c ** 2)

            '''stochastic policy'''
            a_noise, log_prob = self.act.get__a__log_prob_1101(state)
            log_prob = log_prob.mean()

            '''auto temperature parameter: alpha'''
            alpha_loss = lamb * self.log_alpha * (log_prob - self.target_entropy).detach()  # stable
            self.alpha_optimizer.zero_grad()
            alpha_loss.backward()
            self.alpha_optimizer.step()
            with torch.no_grad():
                self.log_alpha[:] = self.log_alpha.clamp(-16, 1)
                alpha = self.log_alpha.exp()  # .detach()

            if update_a / update_c > 1 / (2 - lamb):
                united_loss = critic_loss
            else:
                update_a += 1  # auto TTUR
                '''actor_loss'''
                q_eval_pg = torch.min(*self.act_target.get__q1_q2(state, a_noise)).mean()  # twin critics
                actor_loss = -(q_eval_pg + log_prob * alpha)  # policy gradient

                united_loss = critic_loss + actor_loss * lamb

            self.act_optimizer.zero_grad()
            united_loss.backward()
            self.act_optimizer.step()

            soft_target_update(self.act_target, self.act, tau=2 ** -8)
        return log_prob.item(), critic_loss.item() / 2


def run_continuous_action(gpu_id=None):
    rl_agent = AgentInterSAC1101
    args = Arguments(rl_agent, gpu_id)
    args.if_break_early = False
    args.if_remove_history = True

    # args.env_name = "LunarLanderContinuous-v2"
    # args.break_step = int(5e4 * 16)  # (2e4) 5e4
    # args.reward_scale = 2 ** -3  # (-800) -200 ~ 200 (302)
    # args.init_for_training()
    # train_agent_mp(args)  # train_agent(**vars(args))
    # # exit()
    #
    # args.env_name = "BipedalWalker-v3"
    # args.break_step = int(2e5 * 8)  # (1e5) 2e5
    # args.reward_scale = 2 ** -1  # (-200) -140 ~ 300 (341)
    # args.init_for_training()
    # train_agent_mp(args)  # train_agent(**vars(args))
    # exit()
    #
    # import pybullet_envs  # for python-bullet-gym
    # dir(pybullet_envs)
    # args.env_name = "AntBulletEnv-v0"
    # args.break_step = int(1e6 * 8)  # (8e5) 10e5
    # args.reward_scale = 2 ** -3  # (-50) 0 ~ 2500 (3340)
    # args.batch_size = 2 ** 8
    # args.max_memo = 2 ** 20
    # args.eva_size = 2 ** 3  # for Recorder
    # args.show_gap = 2 ** 8  # for Recorder
    # args.init_for_training()
    # train_agent_mp(args)  # train_agent(**vars(args))
    # exit()

    import pybullet_envs  # for python-bullet-gym
    dir(pybullet_envs)
    args.env_name = "MinitaurBulletEnv-v0"
    args.break_step = int(4e6 * 4)  # (2e6) 4e6
    args.reward_scale = 2 ** 2  # (-2) 0 ~ 16 (20)
    args.batch_size = 2 ** 8
    args.max_memo = 2 ** 20
    args.eval_times2 = 2 ** 5  # for Recorder
    args.show_gap = 2 ** 9  # for Recorder
    args.init_for_training()
    train_agent_mp(args)  # train_agent(**vars(args))
    exit()


run_continuous_action()
