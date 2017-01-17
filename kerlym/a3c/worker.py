import threading, time
import numpy as np
import random

class a3c_learner(threading.Thread):

    def __init__(self, parent, tid):
        threading.Thread.__init__(self)
        self.parent = parent
        self.tid = tid
        self.env = self.parent.env[tid]
        self.config = {
            "ep_max_t":1000,
            }

    def run(self):
        t = 0
        n_ep = 0
        ops = self.parent.graph_ops

        while True:

            # state storage ...
            s_t_single = self.parent.prepare_obs(self.env.reset())
            s_t = np.tile(s_t_single,self.parent.nframes).flatten()
            terminal = False

            # Set up per-episode counters
            ep_reward = 0
            episode_ave_max_q = 0
            episode_ave_min_q = 0
            episode_ave_cost = []
            ep_t = 0
            ep_finished = False

            # set up episode storage
            s_i = [s_t]
            r_i = []
            a_i = []

            # reset local weights to target weights
            w = self.parent.global_params.get_weights()
            if not w == None:
                print("Update local params from global... ")
                w_p,w_v = w
                #ops["set_w_p"](w_p)
                #ops["set_w_v"](w_v)
                ops["set_weights_p"](w_p)
                ops["set_weights_v"](w_v)

            # run an episode
            while not ep_finished:

                # Forward pass: get pi(a_t|s_t,Theta')
                readout_t = ops["pi_values"].eval(session = self.parent.session, feed_dict = {ops["s"] : [s_t]})
                readout_t_norm = readout_t / np.sum(readout_t)

                # Choose next action based on policy gradient selection
                a_t = np.zeros([self.env.action_space.n])
                action_index = np.random.choice(range(self.env.action_space.n), p=readout_t_norm[0])
                a_t[action_index] = 1
                a_i.append(a_t)

                # Gym excecutes action in game environment on behalf of actor-learner
                s_t1_single, r_t, terminal, info = self.env.step(action_index)
                s_t1_single = self.parent.prepare_obs(s_t1_single)
                s_t1 = self.parent.diff_obs(s_t1_single, s_t_single)
                s_t1 = np.concatenate( (s_t[0:(self.parent.nframes-1)*np.product(self.parent.input_dim_orig[1:])], s_t1.flatten() ) )

                # update N-state and diff-state tracking values ...
                s_t = s_t1
                s_t_single = s_t1_single

                # store state/reward values for episode training
                s_i.append(s_t)
                r_i.append(r_t)

                # update timing
                ep_t += 1
                t += 1

                # update stats
                ep_reward += r_t
                episode_ave_max_q += np.max(readout_t)
                episode_ave_min_q += np.min(readout_t)

                if terminal or ep_t > self.config["ep_max_t"]:
                    ep_finished = True

            # Determine end reward ...
            n_ep += 1
            if terminal:
                R = 0
            else:
                # set R from our value fn approx
                R = ops["V_values"].eval(session = self.parent.session, feed_dict = {ops["s"] : [s_t]})


            (grad_p,grad_v) = ([], [])
            w_p = ops["w_p"]()
            w_v = ops["w_v"]()
    
            # Perform updates for each time step
            for t_i in range(ep_t-1,-1,-1):

                # set up params (add 1-long batch dim)
                R = np.array( [r_i[t_i] + self.parent.gamma*R], dtype=np.float32 ).reshape([1,1])
                s = np.expand_dims(s_i[t_i],0)
                a = a_i[t_i].reshape([1,self.env.action_space.n])
                fd = { ops["R"] : R, ops["s"] : s, ops["a"] : a }

                # compute costs for observation
                cost_pi = ops["cost_pi"].eval(session=self.parent.session, feed_dict=fd)
                episode_ave_cost.append(cost_pi)

#                print R.shape, s.shape, a.shape
##                print dir(ops["grad_pi"])
#                print map(lambda x: x.name, ops["grad_pi"].inputs)
##                print dir(ops["grad_pi"].inputs[0])
#                grad_pi = ops["grad_pi"](inputs=w_p)
#                grad_v = ops["grad_V"](inputs=[R, s, a] )

                # accumulate value network gradients
                for i,gv in enumerate(ops["grad_V"]):
                    gv_i = gv.eval(session=self.parent.session, feed_dict=fd)
                    if(len(grad_v) <= i):
                        grad_v.append(gv_i)
                    else:
                        grad_v[i] += gv_i

                # accumulate policy network gradients
                for i,gp in enumerate(ops["grad_pi"]):
                    gp_i = gp.eval(session=self.parent.session, feed_dict=fd)
                    if(len(grad_p) <= i):
                        grad_p.append(gp_i)
                    else:
                        grad_p[i] += gp_i
       

            self.parent.global_params.update( ( (w_p,w_v),(grad_p,grad_v) ) )

            # Save model progress
            if n_ep % self.parent.checkpoint_interval == 0 and self.tid == 0:
                fp = self.parent.checkpoint_dir+"/checkpoint_"+self.parent.experiment+".ckpt"
                print("Writing checkpoint: ", fp)
                self.parent.saver.save(self.parent.session, fp, global_step = t)

            # Print end of episode stats
            stats = {
                'tr': ep_reward,
                'ft':ep_t,
                'maxvf':episode_ave_max_q/float(ep_t),
                'minvf':episode_ave_min_q/float(ep_t),
                'cost':np.mean(episode_ave_cost)
                }
            self.parent.update_stats_threadsafe(stats, self.tid)
            print("THREAD:", self.tid, "/ TIME", self.parent.T, "/ TIMESTEP", t, "/ REWARD", ep_reward, "/ POLICY_MIN-MAX (%.4f, %.4f)" % (episode_ave_min_q/float(ep_t), episode_ave_max_q/float(ep_t)))


class render_thread(threading.Thread):
    def __init__(self, updates_per_sec=10.0, envs = []):
        threading.Thread.__init__(self)
        self.done = False
        self.envs = envs
        self.sleeptime = 1.0/updates_per_sec

    def run(self):
        while not self.done:
            for e in self.envs:
                e.render()
            time.sleep(self.sleeptime)

class plotter_thread(threading.Thread):
    def __init__(self, parent):
        threading.Thread.__init__(self)
        self.parent = parent
        self.done = False

    def run(self):
        while not self.done:
            try:
                st = self.parent.plot_q.get(block=True, timeout=1)
                self.parent.update_stats(st,0)
            except:
                pass
