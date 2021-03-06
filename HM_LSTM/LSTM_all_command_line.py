import numpy as np
import tensorflow as tf
from six.moves import range
from six.moves.urllib.request import urlretrieve
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import codecs
import time
import os
import gc
from six.moves import cPickle as pickle
import sys
import subprocess
if not os.path.isfile('model_module.py') or not os.path.isfile('plot_module.py'):
    current_path = os.path.dirname(os.path.abspath('__file__'))
    additional_path = '/'.join(current_path.split('/')[:-1])
    sys.path.append(additional_path)
from plot_module import text_plot
from plot_module import structure_vocabulary_plots
from plot_module import text_boundaries_plot
from plot_module import ComparePlots

from model_module import maybe_download
from model_module import read_data
from model_module import check_not_one_byte
from model_module import id2char
from model_module import char2id
from model_module import BatchGenerator
from model_module import characters
from model_module import batches2string
from model_module import logprob
from model_module import sample_distribution


from LSTM_all_core import LSTM
version = sys.version_info[0]

class CommandLineInput(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)

if sys.argv[1] == 'dirty':
    if not os.path.exists('enwik8_filtered'):
        if not os.path.exists('enwik8'):
            filename = maybe_download('enwik8.zip', 36445475)
            full_text = read_data(filename)
            f = open('enwik8', 'wb')
            f.write(full_text.encode('utf8'))
            f.close()
        else:
            f = open('enwik8', 'rb')
            full_text = f.read().decode('utf8')
            f.close()
        new_text = u""
        new_text_list = list()
        for i in range(len(full_text)):
            if (i+1) % 10000000 == 0:
                print("%s characters are filtered" % i)
            if ord(full_text[i]) < 256:
                new_text_list.append(full_text[i])
        text = new_text.join(new_text_list)
        del new_text_list
        del new_text
        del full_text

        (not_one_byte_counter, min_character_order_index, max_character_order_index, number_of_characters, present_characters_indices) = check_not_one_byte(text)

        print("number of not one byte characters: ", not_one_byte_counter) 
        print("min order index: ", min_character_order_index)
        print("max order index: ", max_character_order_index)
        print("total number of characters: ", number_of_characters)
    
        f = open('enwik8_filtered', 'w', encoding='utf-8')
        f.write(text)
        f.close()
    
    else:
        f = open('enwik8_filtered', 'r', encoding='utf-8')
        text = f.read()
        f.close() 

elif sys.argv[1] == 'clean':
    if not os.path.exists('enwik8_clean'):
        if not os.path.exists('enwik8'):
            filename = maybe_download('enwik8.zip', 36445475)
            full_text = read_data(filename)
            f = open('enwik8_clean', 'w', encoding='utf-8')
            f.write(full_text)
            f.close()       
        perl_script = subprocess.call(['perl', "clean.pl", 'enwik8', 'enwik8_clean'])
    f = open('enwik8_clean', 'r', encoding='utf-8')
    text = f.read()
    print('length of text:', len(text))
    f.close() 
    (not_one_byte_counter, min_character_order_index, max_character_order_index, number_of_characters, present_characters_indices) = check_not_one_byte(text)

else:
    f = open(sys.argv[1], 'r', encoding='utf-8')
    text = f.read()
    f.close() 


#different
offset = 20000
valid_size = 10000
valid_text = text[offset:offset+valid_size]
train_text = text[offset+valid_size:]
train_size = len(train_text)


# In[5]:

vocabulary = create_vocabulary(text)
vocabulary_size = len(vocabulary)
characters_positions_in_vocabulary = get_positions_in_vocabulary(vocabulary)
print(vocabulary)


init_parameter_value = float(sys.argv[2])
matr_init_parameter_value = float(sys.argv[3])

num_nodes = 128
init_slope = .5
slope_growth = .5
slope_half_life = 1000
results_GL = list()

folder_name = 'nn%sis%ssg%sshl%s' % (num_nodes, init_slope, slope_growth, slope_half_life)
name_of_run = 'ip%s_imp%s' % (init_parameter_value, matr_init_parameter_value)
folder_name = 'nn%sis%ssg%sshl%s' % (num_nodes, init_slope, slope_growth, slope_half_life)
model = LSTM(64,
                                 vocabulary,
                                 characters_positions_in_vocabulary,
                                 30,
                                 3,
                                 [num_nodes, num_nodes, num_nodes],
                                 train_text,
                                 valid_text,
                        init_parameter=init_parameter_value,
                        matr_init_parameter=matr_init_parameter_value)
model.simple_run(100,                # number of percents values used for final averaging
                         'results/LSTM_all/'+ folder_name +'/'+name_of_run+'/variables',
                         10000,              # minimum number of learning iterations
                         20000,              # period of checking loss function. It is used defining if learning should be stopped
                         20000,              # learning has a chance to be stopped after every block of steps
                         10,                 # number of times 'learning_rate' is multiplied on 'decay'
                         .8,                 # a factor by which the learning rate decreases each 'half_life'
                         3,                  # if fixed_num_steps=False this parameter defines when the learning process should be stopped. If during half the total learning time loss function decreased less than by 'stop_percent' percents the learning would be stopped
                         fixed_num_steps=True)
results_GL.append(model._results[-1])
model.destroy()
del model
gc.collect()
pickle_file_name = folder_name
folder_name = 'results/LSTM_all/'+pickle_file_name
file_name = pickle_file_name+'.pickle'
pickle_dump = {'results_GL': results_GL}
if not os.path.exists(folder_name):
    try:
        os.makedirs(folder_name)
    except Exception as e:
        print("Unable create folder '%s'" % folder_name, ':', e)    
print('Pickling %s.' % (folder_name + '/' + file_name))
try:
    with open(folder_name + '/' + file_name, 'wb') as f:
        pickle.dump(pickle_dump, f, pickle.HIGHEST_PROTOCOL)
except Exception as e:
    print('Unable to save data to', file_name, ':', e)



