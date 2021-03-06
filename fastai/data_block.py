from .torch_core import *
from .basic_data import *

__all__ = ['InputList', 'ItemList', 'LabelList', 'PathItemList', 'SplitData', 'SplitDatasets', 'get_files', 'create_sdata']

def get_files(c:PathOrStr, extensions:Collection[str]=None, recurse:bool=False)->FilePathList:
    "Return list of files in `c` that have a suffix in `extensions`. `recurse` determines if we search subfolders."
    return [o for o in Path(c).glob('**/*' if recurse else '*')
            if not o.name.startswith('.') and not o.is_dir()
            and (extensions is None or (o.suffix.lower() in extensions))]

class ItemList():
    "A collection of items with `__len__` and `__getitem__` with `ndarray` indexing semantics."
    def __init__(self, items:Iterator): self.items = np.array(list(items))
    def __len__(self)->int: return len(self.items)
    def __getitem__(self,i:int)->Any: return self.items[i]
    def __repr__(self)->str: return f'{self.__class__.__name__} ({len(self)} items)\n{self.items}'

class PathItemList(ItemList):
    "An `ItemList` with a `path` attribute."
    def __init__(self, items:Iterator, path:PathOrStr='.'):
        super().__init__(items)
        self.path = Path(path)
    def __repr__(self)->str: return f'{super().__repr__()}\nPath: {self.path}'

def _decode(df):
    return np.array([[df.columns[i] for i,t in enumerate(x) if t==1] for x in df.values], dtype=np.object)

def _maybe_squeeze(arr):
    "Squeeze array dimensions but avoid squeezing a 1d-array containing a string."
    return (arr if is1d(arr) else np.squeeze(arr))

def _extract_input_labels(df:pd.DataFrame, input_cols:IntsOrStrs=0, label_cols:IntsOrStrs=1, is_fnames:bool=False,
                      label_delim:str=None, suffix:Optional[str]=None):
    """Get image file names in `fn_col` by adding `suffix` and labels in `label_col` from `df`.
    If `label_delim` is specified, splits the values in `label_col` accordingly.
    """
    assert label_delim is None or not isinstance(label_cols, Iterable) or len(label_cols) == 1
    labels = df.iloc[:,df_names_to_idx(label_cols, df)]
    if label_delim: labels = np.array(list(csv.reader(labels.iloc[:,0], delimiter=label_delim)))
    else: 
        if isinstance(label_cols, Iterable) and len(label_cols) > 1: labels = _decode(labels)
        else: labels = _maybe_squeeze(labels.values)
    inputs = df.iloc[:,df_names_to_idx(input_cols, df)]
    if is_fnames and not isinstance(inputs.iloc[0,0], Path): inputs = inputs.iloc[:,0].str.lstrip()
    if suffix: inputs = inputs + suffix
    return _maybe_squeeze(inputs.values), labels

class InputList(PathItemList):
    "A list of inputs. Contain methods to get the corresponding labels."
    def __init__(self, items:Iterator, path:PathOrStr='.'):
        super().__init__(items,path)
        self._pipe=LabelList

    @classmethod
    def from_folder(cls, path:PathOrStr='.', extensions:Collection[str]=None, recurse=True)->'InputList':
        "Get the list of files in `path` that have a suffix in `extensions`. `recurse` determines if we search subfolders."
        return cls(get_files(path, extensions=extensions, recurse=recurse), path)
    
    @classmethod
    def from_df(cls, df:DataFrame, col:IntsOrStrs=0, path:PathOrStr='.')->'InputList':
        "Get the list of inputs in the `col`of `path/csv_name`."
        inputs = df.iloc[:,df_names_to_idx(col, df)]
        return cls(_maybe_squeeze(inputs.values), path)
    
    @classmethod
    def from_csv(cls, path:PathOrStr, csv_name:str, col:IntsOrStrs=0, header:str='infer')->'InputList':
        "Get the list of inputs in the `col`of `path/csv_name`."
        df = pd.read_csv(path/csv_name, header=header)
        return cls.from_df(df, col, path)
    
    def create_label_list(self, items:Iterator)->'LabelList':
        return self._pipe(items, self.path)
    
    def label_const(self, const:Any=0)->'LabelList':
        "Label every item with `const`."
        return self.create_label_list([(o,const) for o in self.items])

    def label_from_func(self, func:Callable)->'LabelList':
        "Apply `func` to every input to get its label."
        return self.create_label_list([(o,func(o)) for o in self.items])

    def label_from_re(self, pat:str, full_path:bool=False)->'LabelList':
        "Apply the re in `pat` to determine the label of every filename.  If `full_path`, search in the full name."
        pat = re.compile(pat)
        def _inner(o):
            s = str(o if full_path else o.name)
            res = pat.search(s)
            assert res,f'Failed to find "{pat}" in "{s}"'
            return res.group(1)
        return self.label_from_func(_inner)

    def label_from_df(self, df, fn_col:IntsOrStrs=0, label_col:IntsOrStrs=1, sep:str=None, folder:PathOrStr='.',
                      suffix:str=None)->'LabelList':
        "Look in `df` for the filenames in `fn_col` to get the corresponding label in `label_col`."
        fnames, labels = _extract_input_labels(df, fn_col, label_col, label_delim=sep, suffix=suffix, is_fnames=True)
        fnames = join_paths(fnames, self.path/Path(folder))
        df1 = pd.DataFrame({'fnames':fnames, 'labels':labels}, columns=['fnames', 'labels'])
        df2 = pd.DataFrame({'fnames':self.items}, columns=['fnames'])
        inter = pd.merge(df1, df2, how='inner', on=['fnames'])
        return self.create_label_list([(fn, lbl)
            for fn, lbl in zip(inter['fnames'].values, inter['labels'].values)])

    def label_from_csv(self, csv_fname, header:Optional[Union[int,str]]='infer', fn_col:IntsOrStrs=0, label_col:IntsOrStrs=1,
                       sep:str=None, folder:PathOrStr='.', suffix:str=None)->'LabelList':
        """Look in `self.path/csv_fname` for a csv loaded with an optional `header` containing the filenames in
        `fn_col` to get the corresponding label in `label_col`."""
        df = pd.read_csv(self.path/csv_fname, header=header)
        return self.label_from_df(df, fn_col, label_col, sep, folder, suffix)

    def label_from_folder(self, classes:Collection[str]=None)->'LabelList':
        "Give a label to each filename depending on its folder. If `classes` are specified, only keep those."
        labels = [fn.parent.parts[-1] for fn in self.items]
        if classes is None: classes = uniqueify(labels)
        return self.create_label_list([(o,lbl) for o, lbl in zip(self.items, labels) if lbl in classes])

class LabelList(PathItemList):
    "A list of inputs and labels. Contain methods to split it in `SplitData`."
    def __init__(self, items:Iterator, path:PathOrStr='.', parent:InputList=None):
        super().__init__(items=items, path=path)
        self._pipe = SplitData
        self.parent = parent

    @property
    def files(self): return self.items[:,0]

    @property
    def labels(self): return self.items[:,1]

    @classmethod
    def from_df(cls, path:PathOrStr, df:DataFrame, input_cols:IntsOrStrs=0, label_cols:IntsOrStrs=1, label_delim:str=None):
        """Create a `LabelDataset` in `path` by reading `input_cols` and `label_cols` in `df`.
        If `label_delim` is specified, splits the tags in `label_cols` accordingly.
        """
        inputs, labels = _extract_input_labels(df, input_cols, label_cols, label_delim=label_delim)
        return cls.from_lists(path, inputs, labels)

    @classmethod
    def from_csv(cls, path:PathOrStr, csv_fname:PathOrStr, input_cols:IntsOrStrs=0, label_cols:IntsOrStrs=1, header:str='infer',
                 label_delim:str=None):
        """Create a `LabelDataset` in `path` by reading `input_cols` and `label_cols` in the csv in `path/csv_name`
        opened with `header`. If `label_delim` is specified, splits the tags in `label_cols` accordingly.
        """
        df = pd.read_csv(path/csv_fname, header=header)
        return cls.from_df(path, df, input_cols, label_cols, label_delim)

    @classmethod
    def from_csvs(cls, path:PathOrStr, csv_fnames:Collection[PathOrStr], input_cols:IntsOrStrs=0, label_cols:IntsOrStrs=1,
                  header:str='infer')->'LabelList':
        """Create a `LabelDataset` in `path` by reading `input_cols` and `label_cols` in the csvs in `path/csv_names`
        opened with `header`. If `label_delim` is specified, splits the tags in `label_cols` accordingly.
        """
        return cls(np.concatenate([cls.from_csv(path, fname, input_cols, label_cols).items for fname in csv_fnames]), path)

    @classmethod
    def from_lists(cls, path:PathOrStr, inputs, labels)->'LabelList':
        "Create a `LabelDataset` in `path` with `inputs` and `labels`."
        inputs,labels = np.array(inputs),np.array(labels)
        return cls(np.concatenate([inputs[:,None], labels[:,None]], 1), path)
    
    def split_by_list(self, train, valid):
        return self._pipe(self.path, self.__class__(train), self.__class__(valid))

    def split_by_valid_func(self, func:Callable)->'SplitData':
        "Split the data by result of `func` (which returns `True` for validation set)"
        mask = np.array([func(o) for o in self.items])
        return self.split_by_list(self.items[~mask], self.items[mask])

    def split_by_files(self, valid_names:InputList)->'SplitData':
        "Split the data by using the names in `valid_names` for validation."
        valid = [o for o in self.items if o[0] in valid_names]
        train = [o for o in self.items if o[0] not in valid_names]
        return self.split_by_list(train, valid)

    def split_by_fname_file(self, fname:PathOrStr, path:PathOrStr=None)->'SplitData':
        """Split the data by using the file names in `fname` for the validation set. `path` will override `self.path`.
        This method won't work if you inputs aren't filenames.
        """
        path = Path(ifnone(path, self.path))
        valid_names = join_paths(loadtxt_str(self.path/fname), path)
        return self.split_by_files(valid_names)

    def split_by_idx(self, valid_idx:Collection[int])->'SplitData':
        "Split the data according to the indexes in `valid_idx`."
        valid = [o for i,o in enumerate(self.items) if i in valid_idx]
        train = [o for i,o in enumerate(self.items) if i not in valid_idx]
        return self.split_by_list(train, valid)

    def split_by_folder(self, train:str='train', valid:str='valid')->'SplitData':
        """Split the data depending on the folder (`train` or `valid`) in which the filenames are.
        This method won't work if you inputs aren't filenames.
        """
        n = len(self.path.parts)
        folder_name = [o[0].parent.parts[n] for o in self.items]
        valid = [o for o in self.items if o[0].parent.parts[n] == valid]
        train = [o for o in self.items if o[0].parent.parts[n] == train]
        return self.split_by_list(train, valid)

    def random_split_by_pct(self, valid_pct:float=0.2)->'SplitData':
        "Split the items randomly by putting `valid_pct` in the validation set."
        rand_idx = np.random.permutation(range(len(self.items)))
        cut = int(valid_pct * len(self.items))
        return self.split_by_idx(rand_idx[:cut])

class SplitData():
    "A `LabelList` for each of `train` and `valid` (optional `test`), and method to get `datasets`"
    def __init__(self, path:PathOrStr, train:LabelList, valid:LabelList, test:LabelList=None):
        self.path,self.train,self.valid,self.test = Path(path),train,valid,test
        self._pipe = SplitDatasets

    def __repr__(self)->str:
        return f'{self.__class__.__name__}; {self.train}; {self.valid}; {self.test}'

    @classmethod
    def from_csv(cls, path:PathOrStr, csv_fname:PathOrStr, input_cols:IntsOrStrs=0, label_cols:IntsOrStrs=1,
                 valid_col:int=2, header:str='infer')->'SplitData':
        """Create a `SplitData` in `path` from the csv in `path/csv_name` read with `header`. Take the inputs from 
        `input_cols`, the labels from `label_cols` and split by `valid_col` (`True` indicates valid set)."""
        df = pd.read_csv(path/csv_fname, header=header)
        val_idx = df.iloc[:,valid_col].nonzero()[0]
        return LabelList.from_df(path, df, input_cols, label_cols).split_by_idx(val_idx)

    @property
    def lists(self):
        res = [self.train,self.valid]
        if self.test is not None: res.append(self.test)
        return res

    def dataset_cls(self): raise Exception('not implemented')

    def datasets(self, dataset_cls:type=None, **kwargs)->'SplitDatasets':
        "Create datasets from the underlying data using `dataset_cls` and passing along the `kwargs`."
        if dataset_cls is None: dataset_cls = self.dataset_cls()
        train = dataset_cls(*self.train.items.T, **kwargs)
        dss = [train]
        dss += [train.new(*o.items.T, **kwargs) for o in self.lists[1:]]
        cls = getattr(train, '__splits_class__', self._pipe)
        return cls(self.path, *dss)

    def add_test(self, test:ItemList, label:Any=None):
        "Add test set containing items from `test` and an arbitrary label"
        # if no label passed, used label of first training item
        if label is None: label=self.train[0][1]
        self.test = LabelList([(o,label) for o in test], self.path)
        return self

@dataclass
class SplitDatasets():
    "A class regrouping `train_ds`, a `valid_ds` and maybe a `train_ds` dataset, inside a `path`."
    path:PathOrStr
    train_ds:Dataset
    valid_ds:Dataset
    test_ds:Optional[Dataset] = None

    def __post_init__(self): self._pipe = DataBunch

    @property
    def datasets(self)->Collection[Dataset]:
        "The underlying datasets of this object."
        return [self.train_ds,self.valid_ds] if self.test_ds is None else [self.train_ds,self.valid_ds, self.test_ds]

    @datasets.setter
    def datasets(self,ds)->None:
        assert (len(ds) == 2 or len(ds) == 3), "You have to pass two or three datasets."
        self.train_ds,self.valid_ds = ds[:2]
        if len(ds) == 3: self.test_ds = ds[2]

    def set_attr(self, **kwargs):
        "Set the attributes in `kwargs` in the underlying datasets."
        dss = self.datasets
        for key,val in kwargs.items():
            for ds in dss: ds = setattr(ds, key, val)
        self.datasets = dss
        return self

    def dataloaders(self, **kwargs)->Collection[DataLoader]:
        "Create dataloaders with the inner datasets, pasing the `kwargs`."
        return [DataLoader(o, **kwargs) for o in self.datasets]

    @classmethod
    def from_single(cls, path:PathOrStr, ds:Dataset)->'SplitDatasets':
        "Factory method that uses `ds` for both valid and train, and passes `path`."
        return cls(path, ds, ds)

    @classmethod
    def single_from_classes(cls, path:PathOrStr, classes:Collection[str])->'SplitDatasets':
        "Factory method that passes a `SingleClassificationDataset` on `classes` to `from_single`."
        return cls.from_single(path, SingleClassificationDataset(classes))

    @classmethod
    def single_from_c(cls, path:PathOrStr, c:int)->'SplitDatasets':
        "Factory method that passes a `DatasetBase` on `c` to `from_single`."
        return cls.from_single(path, DatasetBase([0], c=c))

def create_sdata(sdata_cls, path:PathOrStr, train_x:Collection, train_y:Collection, valid_x:Collection, 
                 valid_y:Collection, test_x:Collection=None):
    train = LabelList.from_lists(path, train_x, train_y)
    valid = LabelList.from_lists(path, valid_x, valid_y)
    test = InputList(test_x, path).label_const(0) if test_x is not None else None
    return SplitData(path, train, valid, test)