from __future__ import print_function
import numpy as np
import tensorflow as tf
from some_useful_functions import (construct, create_vocabulary,
                                   get_positions_in_vocabulary, char2vec, pred2vec, vec2char,
                                   char2id, id2char, flatten, get_available_gpus, device_name_scope,
                                   average_gradients, get_num_gpus_and_bs_on_gpus)


url = 'http://mattmahoney.net/dc/'


class BatchGenerator(object):

    @staticmethod
    def create_vocabulary(texts):
        text = ''
        for t in texts:
            text += t
        return create_vocabulary(text)

    @staticmethod
    def char2vec(char, characters_positions_in_vocabulary, speaker_idx, speaker_flag_size):
        return np.reshape(char2vec(char, characters_positions_in_vocabulary), (1, 1, -1))

    @staticmethod
    def pred2vec(pred, speaker_idx, speaker_flag_size, batch_gen_args):
        return np.reshape(pred2vec(pred), (1, 1, -1))

    @staticmethod
    def vec2char(vec, vocabulary):
        return vec2char(vec, vocabulary)

    def __init__(self, text, batch_size, num_unrollings=1, vocabulary=None):
        self._text = text
        self._text_size = len(text)
        self._batch_size = batch_size
        self.vocabulary = vocabulary
        self._vocabulary_size = len(self.vocabulary)
        self.characters_positions_in_vocabulary = get_positions_in_vocabulary(self.vocabulary)
        self._num_unrollings = num_unrollings
        segment = self._text_size // batch_size
        self._cursor = [offset * segment for offset in range(batch_size)]
        self._last_batch = self._start_batch()

    def get_dataset_length(self):
        return len(self._text)

    def get_vocabulary_size(self):
        return self._vocabulary_size

    def _start_batch(self):
        batch = np.zeros(shape=(self._batch_size, self._vocabulary_size), dtype=np.float)
        for b in range(self._batch_size):
            batch[b, char2id('\n', self.characters_positions_in_vocabulary)] = 1.0
        return batch

    def _zero_batch(self):
        return np.zeros(shape=(self._batch_size, self._vocabulary_size), dtype=np.float)

    def _next_batch(self):
        """Generate a single batch from the current cursor position in the data."""
        batch = np.zeros(shape=(self._batch_size, self._vocabulary_size), dtype=np.float)
        for b in range(self._batch_size):
            batch[b, char2id(self._text[self._cursor[b]], self.characters_positions_in_vocabulary)] = 1.0
            self._cursor[b] = (self._cursor[b] + 1) % self._text_size
        return batch

    def char2batch(self, char):
        return np.stack(char2vec(char, self.characters_positions_in_vocabulary)), np.stack(self._zero_batch())

    def pred2batch(self, pred):
        batch = np.zeros(shape=(self._batch_size, self._vocabulary_size), dtype=np.float)
        char_id = np.argmax(pred, 1)[-1]
        batch[0, char_id] = 1.0
        return np.stack([batch]), np.stack([self._zero_batch()])

    def next(self):
        """Generate the next array of batches from the data. The array consists of
        the last batch of the previous array, followed by num_unrollings new ones.
        """
        batches = [self._last_batch]
        for step in range(self._num_unrollings):
            batches.append(self._next_batch())
        self._last_batch = batches[-1]
        return np.stack(batches[:-1]), np.concatenate(batches[1:], 0)


def characters(probabilities, vocabulary):
    """Turn a 1-hot encoding or a probability distribution over the possible
    characters back into its (most likely) character representation."""
    return [id2char(c, vocabulary) for c in np.argmax(probabilities, 1)]


def batches2string(batches, vocabulary):
    """Convert a sequence of batches back into their (most likely) string
    representation."""
    s = [u""] * batches[0].shape[0]
    for b in batches:
        s = [u"".join(x) for x in zip(s, characters(b, vocabulary))]
    return s


class Model(object):

    @classmethod
    def get_name(cls):
        return cls.name


class Gru(Model):
    _name = 'gru'

    @classmethod
    def check_kwargs(cls,
                     **kwargs):
        pass

    @classmethod
    def get_name(cls):
        return cls._name

    @staticmethod
    def get_special_args():
        return dict()

    @staticmethod
    def form_kwargs(kwargs_for_building, insertions):
        for insertion in insertions:
            if insertion['list_index'] is None:
                kwargs_for_building[insertion['hp_name']] = insertion['paste']
            else:
                kwargs_for_building[insertion['hp_name']][insertion['list_index']] = insertion['paste']
        return kwargs_for_building

    def _gru_layer(self, inp, state, layer_idx):
        with tf.name_scope('gru_layer_%s' % layer_idx):
            gate_matr = self._gate_matrices[layer_idx]
            main_matr = self._main_matrices[layer_idx]
            drop_inp = tf.nn.dropout(inp, self.dropout_keep_prob, name='drop_inp')

            x_gate = tf.concat([drop_inp, state], 1, name='x_gate')
            gate_res = tf.sigmoid(tf.matmul(x_gate, gate_matr, name='matmul_gate'), name='gate_concat')
            z_gate, r_gate = tf.split(gate_res, 2, axis=1, name='gates')

            x_main = tf.concat([drop_inp, tf.multiply(state, r_gate, name='gated_state')],
                               1,
                               name='x_main')
            main_res = tf.tanh(tf.matmul(x_main, main_matr, name='matmul_main'), name='main_res')

            new_state = tf.add(tf.multiply(tf.subtract(1.,
                                                       z_gate,
                                                       name='reversed_z_gate'),
                                           state, name='reversed_gated_state'),
                               tf.multiply(z_gate, main_res, name='gated_main_res'),
                               name='new_state')
        return new_state, new_state

    def _rnn_iter(self, embedding, all_states):
        with tf.name_scope('rnn_iter'):
            new_all_states = list()
            output = embedding
            for layer_idx, state in enumerate(all_states):
                output, state = self._gru_layer(output, state, layer_idx)
                new_all_states.append(state)
            return output, new_all_states

    def _rnn_module(self, embeddings, all_states):
        rnn_outputs = list()
        with tf.name_scope('rnn_module'):
            for emb in embeddings:
                rnn_output, all_states = self._rnn_iter(emb, all_states)
                #print('rnn_output.shape:', rnn_output.get_shape().as_list())
                rnn_outputs.append(rnn_output)
        return rnn_outputs, all_states

    def _embed(self, inputs):
        with tf.name_scope('embeddings'):
            num_unrollings = len(inputs)
            inputs = tf.concat(inputs, 0, name='concatenated_inputs')
            embeddings = tf.matmul(inputs, self._embedding_matrix, name='embeddings_stacked')
            return tf.split(embeddings, num_unrollings, 0, name='embeddings')

    def _output_module(self, rnn_outputs):
        with tf.name_scope('output_module'):
            #print('rnn_outputs:', rnn_outputs)
            rnn_outputs = tf.concat(rnn_outputs, 0, name='concatenated_rnn_outputs')
            hs = rnn_outputs
            for layer_idx in range(self._num_output_layers):
                #print('hs.shape:', hs.get_shape().as_list())
                hs = tf.add(
                    tf.matmul(hs,
                              self._output_matrices[layer_idx],
                              name='matmul_in_%s_output_layer' % layer_idx),
                    self._output_biases[layer_idx],
                    name='res_of_%s_output_layer' % layer_idx)
                if layer_idx < self._num_output_layers - 1:
                    hs = tf.nn.relu(hs)
        return hs

    @staticmethod
    def _extract_op_name(full_name):
        scopes_stripped = full_name.split('/')[-1]
        return scopes_stripped.split(':')[0]

    def _compose_save_list(self,
                           *pairs):
        #print('start')
        with tf.name_scope('save_list'):
            save_list = list()
            for pair in pairs:
                #print('pair:', pair)
                variables = flatten(pair[0])
                #print(variables)
                new_values = flatten(pair[1])
                for variable, value in zip(variables, new_values):
                    name = self._extract_op_name(variable.name)
                    save_list.append(tf.assign(variable, value, name='assign_save_%s' % name))
            return save_list

    def _compose_reset_list(self, *args):
        with tf.name_scope('reset_list'):
            reset_list = list()
            flattened = flatten(args)
            for variable in flattened:
                shape = variable.get_shape().as_list()
                name = self._extract_op_name(variable.name)
                reset_list.append(tf.assign(variable, tf.zeros(shape), name='assign_reset_%s' % name))
            return reset_list

    def _compose_randomize_list(self, *args):
        with tf.name_scope('randomize_list'):
            randomize_list = list()
            flattened = flatten(args)
            for variable in flattened:
                shape = variable.get_shape().as_list()
                name = self._extract_op_name(variable.name)
                assign_tensor = tf.truncated_normal(shape, stddev=1.)
                #assign_tensor = tf.Print(assign_tensor, [assign_tensor], message='assign tensor:')
                assign = tf.assign(variable, assign_tensor, name='assign_reset_%s' % name)
                randomize_list.append(assign)
            return randomize_list

    def _compute_gate_matrix_parameters(self, idx):
        if idx == 0:
            input_dim = self._num_nodes[0] + self._embedding_size
        else:
            input_dim = self._num_nodes[idx - 1] + self._num_nodes[idx]
        output_dim = 2 * self._num_nodes[idx]
        stddev = self._init_parameter * np.sqrt(1./input_dim)
        return input_dim, output_dim, stddev

    def _compute_main_matrix_parameters(self, idx):
        if idx == 0:
            input_dim = self._num_nodes[0] + self._embedding_size
        else:
            input_dim = self._num_nodes[idx - 1] + self._num_nodes[idx]
        output_dim = self._num_nodes[idx]
        stddev = self._init_parameter * np.sqrt(1./input_dim)
        return input_dim, output_dim, stddev

    def _compute_output_matrix_parameters(self, idx):
        if idx == 0:
            #print('self._num_nodes:', self._num_nodes)
            input_dim = self._num_nodes[-1]
        else:
            input_dim = self._num_output_nodes[idx-1]
        if idx == self._num_output_layers - 1:
            output_dim = self._vocabulary_size
        else:
            output_dim = self._num_output_nodes[idx]
        stddev = self._init_parameter * np.sqrt(1. / input_dim)
        return input_dim, output_dim, stddev

    def _l2_loss(self, matrices):
        with tf.name_scope('l2_loss'):
            regularizer = tf.contrib.layers.l2_regularizer(.5)
            loss = 0
            for matr in matrices:
                loss += regularizer(matr)
            return loss * self._regularization_rate

    def _train_graph(self):
        tower_grads = list()
        preds = list()
        losses = list()
        for gpu_batch_size, gpu_name, device_inputs, device_labels in zip(
                self._batch_sizes_on_gpus, self._gpu_names, self._inputs_by_device, self._labels_by_device):
            with tf.device(gpu_name):
                with tf.name_scope(device_name_scope(gpu_name)):
                    with tf.name_scope('train'):
                        saved_states = list()
                        for layer_idx, layer_num_nodes in enumerate(self._num_nodes):
                            saved_states.append(
                                tf.Variable(
                                    tf.zeros([gpu_batch_size, layer_num_nodes]),
                                    trainable=False,
                                    name='saved_state_%s' % layer_idx))

                        all_states = saved_states
                        embeddings = self._embed(device_inputs)
                        rnn_outputs, all_states = self._rnn_module(embeddings, all_states)
                        logits = self._output_module(rnn_outputs)

                        save_ops = self._compose_save_list((saved_states, all_states))

                        with tf.control_dependencies(save_ops):
                            all_matrices = [self._embedding_matrix]
                            all_matrices.extend(self._gate_matrices)
                            all_matrices.extend(self._main_matrices)
                            all_matrices.extend(self._output_matrices)
                            l2_loss = self._l2_loss(all_matrices)

                            loss = tf.reduce_mean(
                                tf.nn.softmax_cross_entropy_with_logits(labels=device_labels, logits=logits))
                            losses.append(loss)
                            # optimizer = tf.train.GradientDescentOptimizer(self.learning_rate)
                            optimizer = tf.train.AdamOptimizer(learning_rate=self.learning_rate)
                            grads_and_vars = optimizer.compute_gradients(loss + l2_loss)
                            tower_grads.append(grads_and_vars)

                            # splitting concatenated results for different characters
                            concat_pred = tf.nn.softmax(logits)
                            preds.append(tf.split(concat_pred, self._num_unrollings))

        with tf.device('/cpu:0'):
            with tf.name_scope(device_name_scope('/cpu:0') + '_gradients'):
                grads_and_vars = average_gradients(tower_grads)
                grads, v = zip(*grads_and_vars)
                grads, _ = tf.clip_by_global_norm(grads, 1.)
                self.train_op = optimizer.apply_gradients(zip(grads, v))
                self._hooks['train_op'] = self.train_op

                # composing predictions
                preds_by_char = list()
                # print('preds:', preds)
                for one_char_preds in zip(*preds):
                    # print('one_char_preds:', one_char_preds)
                    preds_by_char.append(tf.concat(one_char_preds, 0))
                # print('len(preds_by_char):', len(preds_by_char))
                self.predictions = tf.concat(preds_by_char, 0)
                self._hooks['predictions'] = self.predictions
                # print('self.predictions.get_shape().as_list():', self.predictions.get_shape().as_list())
                l = 0
                for loss, gpu_batch_size in zip(losses, self._batch_sizes_on_gpus):
                    l += float(gpu_batch_size) / float(self._batch_size) * loss
                self.loss = l
                self._hooks['loss'] = self.loss

    def _validation_graph(self):
        with tf.device(self._gpu_names[0]):
            with tf.name_scope('validation'):
                self.sample_input = tf.placeholder(tf.float32,
                                                   shape=[1, 1, self._vocabulary_size],
                                                   name='sample_input')
                self._hooks['validation_inputs'] = self.sample_input

                sample_input = tf.reshape(self.sample_input, [1, -1])
                saved_sample_state = list()
                for layer_idx, layer_num_nodes in enumerate(self._num_nodes):
                    saved_sample_state.append(
                        tf.Variable(
                            tf.zeros([1, layer_num_nodes]),
                            trainable=False,
                            name='saved_sample_state_%s' % layer_idx))

                reset_list = self._compose_reset_list(saved_sample_state)

                self.reset_sample_state = tf.group(*reset_list)
                self._hooks['reset_validation_state'] = self.reset_sample_state

                randomize_list = self._compose_randomize_list(saved_sample_state)

                self.randomize = tf.group(*randomize_list)
                self._hooks['randomize_sample_state'] = self.randomize

                embeddings = self._embed([sample_input])
                # print('embeddings:', embeddings)
                rnn_output, sample_state = self._rnn_module(embeddings, saved_sample_state)
                sample_logit = self._output_module(rnn_output)

                sample_save_ops = self._compose_save_list((saved_sample_state, sample_state))

                with tf.control_dependencies(sample_save_ops):
                    self.sample_prediction = tf.nn.softmax(sample_logit)
                    self._hooks['validation_predictions'] = self.sample_prediction

    def __init__(self,
                 batch_size=64,
                 num_layers=2,
                 num_nodes=[112, 113],
                 num_output_layers=1,
                 num_output_nodes=[],
                 vocabulary_size=None,
                 embedding_size=128,
                 num_unrollings=10,
                 init_parameter=3.,
                 num_gpus=1,
                 regularization_rate=.000003,
                 regime='train',
                 going_to_limit_memory=False):

        self._hooks = dict(inputs=None,
                           labels=None,
                           train_op=None,
                           learning_rate=None,
                           loss=None,
                           predictions=None,
                           validation_inputs=None,
                           validation_predictions=None,
                           reset_validation_state=None,
                           randomize_sample_state=None,
                           dropout=None,
                           saver=None)

        self._batch_size = batch_size
        self._num_layers = num_layers
        self._num_nodes = num_nodes
        self._vocabulary_size = vocabulary_size
        self._embedding_size = embedding_size
        self._num_output_layers = num_output_layers
        self._num_output_nodes = num_output_nodes
        self._num_unrollings = num_unrollings
        self._init_parameter = init_parameter
        self._regularization_rate = regularization_rate

        if not going_to_limit_memory:
            self._gpu_names = get_available_gpus()
        else:
            self._gpu_names = ['/gpu:%s' % i for i in range(num_gpus)]
        num_available_gpus = len(self._gpu_names)
        num_gpus, self._batch_sizes_on_gpus = get_num_gpus_and_bs_on_gpus(self._batch_size, num_gpus, num_available_gpus)
        self._num_gpus = num_gpus

        with tf.device('/cpu:0'):
            self.dropout_keep_prob = tf.placeholder(tf.float32, name='dropout_keep_prob')

            self.inputs = tf.placeholder(tf.float32,
                                         shape=[self._num_unrollings, self._batch_size, self._vocabulary_size])
            self.labels = tf.placeholder(tf.float32,
                                         shape=[self._num_unrollings * self._batch_size, self._vocabulary_size])


            #in_flags
            self._hooks['dropout'] = self.dropout_keep_prob
            self._hooks['inputs'] = self.inputs
            self._hooks['labels'] = self.labels


            inputs = tf.split(self.inputs, self._batch_sizes_on_gpus, 1)
            self._inputs_by_device = list()
            for dev_idx, device_inputs in enumerate(inputs):
                self._inputs_by_device.append(tf.unstack(device_inputs, name='inp_on_dev_%s' % dev_idx))

            labels = tf.reshape(self.labels, shape=(self._num_unrollings, self._batch_size, self._vocabulary_size))
            labels = tf.split(labels, self._batch_sizes_on_gpus, 1)
            self._labels_by_device = list()
            for dev_idx, device_labels in enumerate(labels):
                self._labels_by_device.append(
                    tf.reshape(device_labels,
                               [-1, self._vocabulary_size],
                               name='labels_on_dev_%s' % dev_idx))

            self.learning_rate = tf.placeholder(tf.float32, name='learning_rate')
            self._hooks['learning_rate'] = self.learning_rate

            self._embedding_matrix = tf.Variable(
                tf.truncated_normal([self._vocabulary_size, self._embedding_size],
                                    stddev=self._init_parameter*np.sqrt(1./self._vocabulary_size)),
                name='embedding_matrix')

            self._gate_matrices = list()
            for layer_idx in range(self._num_layers):
                input_dim, output_dim, stddev = self._compute_gate_matrix_parameters(layer_idx)
                self._gate_matrices.append(tf.Variable(tf.truncated_normal([input_dim,
                                                                            output_dim],
                                                                           stddev=stddev),
                                                       name='gate_matrix_%s' % layer_idx))
            self._main_matrices = list()
            for layer_idx in range(self._num_layers):
                input_dim, output_dim, stddev = self._compute_main_matrix_parameters(layer_idx)
                self._main_matrices.append(tf.Variable(tf.truncated_normal([input_dim,
                                                                            output_dim],
                                                                           stddev=stddev),
                                                       name='main_matrix_%s' % layer_idx))
            self._output_matrices = list()
            self._output_biases = list()
            for layer_idx in range(self._num_output_layers):
                input_dim, output_dim, stddev = self._compute_output_matrix_parameters(layer_idx)
                #print('input_dim:', input_dim)
                #print('output_dim:', output_dim)
                self._output_matrices.append(tf.Variable(tf.truncated_normal([input_dim, output_dim],
                                                                             stddev=stddev),
                                                         name='output_matrix_%s' % layer_idx))
                self._output_biases.append(tf.Variable(tf.zeros([output_dim])))

            saved_vars = dict()
            saved_vars['embedding_matrix'] = self._embedding_matrix
            for layer_idx, gate_matrix in enumerate(self._gate_matrices):
                saved_vars['gate_matrix_%s' % layer_idx] = gate_matrix
            for layer_idx, main_matrix in enumerate(self._main_matrices):
                saved_vars['main_matrix_%s' % layer_idx] = main_matrix
            for layer_idx, (output_matrix, output_bias) in enumerate(zip(self._output_matrices, self._output_biases)):
                saved_vars['output_matrix_%s' % layer_idx] = output_matrix
                saved_vars['output_bias_%s' % layer_idx] = output_bias
            self.saver = tf.train.Saver(saved_vars, max_to_keep=None)
            self._hooks['saver'] = self.saver

        if regime == 'train':
            self._train_graph()
            self._validation_graph()
        if regime == 'inference':
            self._validation_graph()

    def get_default_hooks(self):
        return dict(self._hooks.items())

    def get_building_parameters(self):
        pass
