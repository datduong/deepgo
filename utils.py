from collections import deque
from keras import backend as K
from keras.callbacks import ModelCheckpoint
import warnings
import pandas as pd


BIOLOGICAL_PROCESS = 'GO:0008150'
MOLECULAR_FUNCTION = 'GO:0003674'
CELLULAR_COMPONENT = 'GO:0005575'
FUNC_DICT = {
    'cc': CELLULAR_COMPONENT,
    'mf': MOLECULAR_FUNCTION,
    'bp': BIOLOGICAL_PROCESS}
EXP_CODES = set(['EXP', 'IDA', 'IPI', 'IMP', 'IGI', 'IEP', 'TAS', 'IC'])


def get_ipro():
    ipro = dict()
    ipros = list()
    with open('data/interpro.txt', 'r') as f:
        for line in f:
            items = line.strip().split('::')
            ipros.append(items)

    def read_tree(parent, i):
        level = ipros[i][0].rfind('-')
        ipro_id = ipros[i][0][level + 1:]
        name = ipros[i][1]
        obj = {'id': ipro_id, 'name': name, 'children': list(), 'parent': None}
        if parent is not None:
            parent['children'].append(ipro_id)
            obj['parent'] = parent['id']
        ipro[ipro_id] = obj
        if i + 1 < len(ipros):
            next_ipro_level = ipros[i + 1][0].rfind('-')
            if next_ipro_level == -1:
                return
            elif level < next_ipro_level:
                read_tree(obj, i + 1)
            elif level == next_ipro_level:
                read_tree(parent, i + 1)
            elif ipro[parent['parent']] is not None:
                read_tree(ipro[parent['parent']], i + 1)
    for i in range(len(ipros)):
        if ipros[i][0].rfind('-') == -1:
            read_tree(None, i)
    return ipro


def get_ipro_anchestors(ipro, ipro_id):
    ipro_set = set()
    q = deque()
    q.append(ipro_id)
    while(len(q) > 0):
        i_id = q.popleft()
        ipro_set.add(i_id)
        if ipro[i_id]['parent']:
            for parent_id in ipro[i_id]['parent']:
                if parent_id in ipro:
                    q.append(parent_id)
    return ipro_set


def get_gene_ontology(filename='go.obo'):
    # Reading Gene Ontology from OBO Formatted file
    go = dict()
    obj = None
    with open('data/' + filename, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line == '[Term]':
                if obj is not None:
                    go[obj['id']] = obj
                obj = dict()
                obj['is_a'] = list()
                obj['part_of'] = list()
                obj['regulates'] = list()
                obj['is_obsolete'] = False
                continue
            elif line == '[Typedef]':
                obj = None
            else:
                if obj is None:
                    continue
                l = line.split(": ")
                if l[0] == 'id':
                    obj['id'] = l[1]
                elif l[0] == 'is_a':
                    obj['is_a'].append(l[1].split(' ! ')[0])
                elif l[0] == 'name':
                    obj['name'] = l[1]
                elif l[0] == 'is_obsolete' and l[1] == 'true':
                    obj['is_obsolete'] = True
    if obj is not None:
        go[obj['id']] = obj
    for go_id in go.keys():
        if go[go_id]['is_obsolete']:
            del go[go_id]
    for go_id, val in go.iteritems():
        if 'children' not in val:
            val['children'] = set()
        for p_id in val['is_a']:
            if p_id in go:
                if 'children' not in go[p_id]:
                    go[p_id]['children'] = set()
                go[p_id]['children'].add(go_id)
    return go


def get_anchestors(go, go_id):
    go_set = set()
    q = deque()
    q.append(go_id)
    while(len(q) > 0):
        g_id = q.popleft()
        go_set.add(g_id)
        for parent_id in go[g_id]['is_a']:
            if parent_id in go:
                q.append(parent_id)
    return go_set


def get_parents(go, go_id):
    go_set = set()
    for parent_id in go[go_id]['is_a']:
        if parent_id in go:
            go_set.add(parent_id)
    return go_set


def get_go_set(go, go_id):
    go_set = set()
    q = deque()
    q.append(go_id)
    while len(q) > 0:
        g_id = q.popleft()
        go_set.add(g_id)
        for ch_id in go[g_id]['children']:
            q.append(ch_id)
    return go_set


def save_model_weights(model, filepath):
    if hasattr(model, 'flattened_layers'):
        # Support for legacy Sequential/Merge behavior.
        flattened_layers = model.flattened_layers
    else:
        flattened_layers = model.layers

    l_names = []
    w_values = []
    for layer in flattened_layers:
        layer_name = layer.name
        symbolic_weights = layer.weights
        weight_values = K.batch_get_value(symbolic_weights)
        if weight_values:
            l_names.append(layer_name)
            w_values.append(weight_values)
    df = pd.DataFrame({
        'layer_names': l_names,
        'weight_values': w_values})
    df.to_pickle(filepath)


def load_model_weights(model, filepath):
    ''' Name-based weight loading
    Layers that have no matching name are skipped.
    '''
    if hasattr(model, 'flattened_layers'):
        # Support for legacy Sequential/Merge behavior.
        flattened_layers = model.flattened_layers
    else:
        flattened_layers = model.layers

    df = pd.read_pickle(filepath)

    # Reverse index of layer name to list of layers with name.
    index = {}
    for layer in flattened_layers:
        if layer.name:
            index[layer.name] = layer

    # We batch weight value assignments in a single backend call
    # which provides a speedup in TensorFlow.
    weight_value_tuples = []
    for row in df.iterrows():
        row = row[1]
        name = row['layer_names']
        weight_values = row['weight_values']
        if name in index:
            symbolic_weights = index[name].weights
            if len(weight_values) != len(symbolic_weights):
                raise Exception('Layer named "' + layer.name +
                                '") expects ' + str(len(symbolic_weights)) +
                                ' weight(s), but the saved weights' +
                                ' have ' + str(len(weight_values)) +
                                ' element(s).')
            # Set values.
            for i in range(len(weight_values)):
                weight_value_tuples.append(
                    (symbolic_weights[i], weight_values[i]))
    K.batch_set_value(weight_value_tuples)


def f_score(labels, preds):
    preds = K.round(preds)
    tp = K.sum(labels * preds)
    fp = K.sum(preds) - tp
    fn = K.sum(labels) - tp
    p = tp / (tp + fp)
    r = tp / (tp + fn)
    return 2 * p * r / (p + r)


class MyCheckpoint(ModelCheckpoint):
    def on_epoch_end(self, epoch, logs={}):
        filepath = self.filepath.format(epoch=epoch, **logs)
        current = logs.get(self.monitor)
        if current is None:
            warnings.warn('Can save best model only with %s available, '
                          'skipping.' % (self.monitor), RuntimeWarning)
        else:
            if self.monitor_op(current, self.best):
                if self.verbose > 0:
                    print('Epoch %05d: %s improved from %0.5f to %0.5f,'
                          ' saving model to %s'
                          % (epoch, self.monitor, self.best,
                             current, filepath))
                self.best = current
                save_model_weights(self.model, filepath)
            else:
                if self.verbose > 0:
                    print('Epoch %05d: %s did not improve' %
                          (epoch, self.monitor))


class DataGenerator(object):

    def __init__(self, batch_size, num_outputs):
        self.batch_size = batch_size
        self.num_outputs = num_outputs

    def fit(self, inputs, targets):
        self.start = 0
        self.inputs = inputs
        self.targets = targets
        self.size = len(self.inputs)
        if isinstance(self.inputs, tuple) or isinstance(self.inputs, list):
            self.size = len(self.inputs[0])

    def __next__(self):
        return self.next()

    def reset(self):
        self.start = 0

    def next(self):
        if self.start < self.size:
            output = []
            if self.targets:
                labels = self.targets
                for i in range(self.num_outputs):
                    output.append(
                        labels[self.start:(self.start + self.batch_size), i])
            if isinstance(self.inputs, tuple) or isinstance(self.inputs, list):
                res_inputs = []
                for inp in self.inputs:
                    res_inputs.append(
                        inp[self.start:(self.start + self.batch_size)])
            else:
                res_inputs = self.inputs[self.start:(
                    self.start + self.batch_size)]
            self.start += self.batch_size
            if self.targets:
                return (res_inputs, output)
            return res_inputs
        else:
            self.reset()
            return self.next()
