import pandas as pd
import numpy as np
import copy
import pdb
import re
import yaml
from datetime import datetime
import logging
from itertools import product
from dateutil.relativedelta import relativedelta

from . import  officer

log = logging.getLogger(__name__)

class EISExperiment(object):
   """The EISExperiment class defines each individual experiment
   Attributes:
       config: dict containing configuration
       exp_data: dict containing data
       pilot_data: dict containing data for pilot if defined
   """

   def __init__(self, config):
       self.config = config.copy()
       self.exp_data = None
       self.pilot_data = None


def read_yaml(config_file_name):
    """
    This function reads the config file 
    Args:
       config_file_name (str): name of the config file
    """
    with open(config_file_name, 'r') as f:
        config = yaml.load(f)
    return config


def relative_deltas_conditions(str_times):
    """
    Function that given a list of string time intervals 
    ej: ['1d','1w','1m','1y'] and returns a dictionary 
    with the string time intervals as key and the relative
    deltas as values
    Args:
      str_times (list): list of string time intervals
    """
    dict_abbreviations = {'h':'hours',
                          'd':'days',
                          'w':'weeks', 
                          'm':'months', 
                          'y':'years'}

    time_deltas = {}
    try:
        for str_time in str_times:
            try:
                units = re.findall(r'\d+(\w)', str_time)[0]
            except:
                raise ValueError('Could not parse units from prediction_window string')
    
            try:
                value = int(re.findall(r'\d+', str_time)[0])
            except:
                raise ValueError('Could not parse value from prediction_window string')
            
            time_deltas.update({str_time: { dict_abbreviations[units]: value }})
    except:
        raise ValueError('Could not parse value for window')
    
    return time_deltas


def as_of_dates_in_window(start_date, end_date, window):
    """
    Generate a list of as_of_dates between start_date and end_date 
    moving through a frequency window
    Args:
       start_date (datetime): 
    """
    # Generate condition for relative delta
    window_delta = relative_deltas_conditions([window])

    as_of_dates = []
    while end_date >= start_date:
        as_of_date = end_date
        end_date -= relativedelta(**window_delta[window])
        as_of_dates.append(as_of_date)
   
    time_format = "%Y-%m-%d"
    as_of_dates_uniques = set(as_of_dates)
    as_of_dates_uniques = [ as_of_date.strftime(time_format) for as_of_date in as_of_dates_uniques]
    return sorted(as_of_dates_uniques)

def generate_temporal_info(temporal_config):
    """
    Returns a list of all temporal sets that are given all
    posssibles temporal combinations generated by config
    Args:
       temporal_config (dict): temporal configuration
    Example:
        temporal_config = {'prediction_window': ['1y'],
                  'update_window': ['1d'],
                  'train_size': ['2y'],
                  'features_frequency': ['3m','1m'],
                  'test_frequency': ['1d'],
                  'test_time_ahead': ['3m'],
                  'officer_past_activity_window': ['1y']}
    """
    time_format = "%Y-%m-%d"
    end_date = datetime.strptime(temporal_config['end_date'], "%Y-%m-%d")
    start_date = datetime.strptime(temporal_config['start_date'], "%Y-%m-%d")

    # convert windows to relativetime deltas
    prediction_window_deltas = relative_deltas_conditions(temporal_config['prediction_window'])
    update_window_deltas = relative_deltas_conditions(temporal_config['update_window'])
    train_size_deltas = relative_deltas_conditions(temporal_config['train_size'])
    features_frequency_deltas = relative_deltas_conditions(temporal_config['features_frequency'])
    test_frequency_deltas = relative_deltas_conditions(temporal_config['test_frequency'])
    test_time_ahead_deltas = relative_deltas_conditions(temporal_config['test_time_ahead'])

    # Loop across all prediction, update, train. features and test windows
    temporal_info = [] 
    for prediction_window, update_window, officer_past_activity, \
        train_size, features_frequency, test_frequency, test_time_ahead \
             in product(    
               temporal_config['prediction_window'], temporal_config['update_window'],
               temporal_config['officer_past_activity_window'], temporal_config['train_size'],
               temporal_config['features_frequency'], temporal_config['test_frequency'],
               temporal_config['test_time_ahead']):

        test_end_date = end_date
        # loop moving giving an update_window
        while start_date <= test_end_date - 2*relativedelta(**prediction_window_deltas[prediction_window]):

            test_start_date = test_end_date - relativedelta(**test_time_ahead_deltas[test_time_ahead])
            test_as_of_dates = as_of_dates_in_window(test_start_date,
                                                     test_end_date,
                                                     test_frequency)

            train_end_date = test_start_date  - relativedelta(**prediction_window_deltas[prediction_window])
            train_start_date = train_end_date - relativedelta(**train_size_deltas[train_size])
            train_as_of_dates = as_of_dates_in_window(train_start_date,
                                                      train_end_date,
                                                      features_frequency)

            tmp_info = {'test_end_date': test_end_date.strftime(time_format),
                        'test_start_date': test_start_date.strftime(time_format),
                        'test_as_of_dates': test_as_of_dates,
                        'train_end_date': train_end_date.strftime(time_format),
                        'train_start_date': train_start_date.strftime(time_format),
                        'train_as_of_dates': train_as_of_dates,
                        'train_size': train_size,
                        'features_frequency': features_frequency,
                        'prediction_window':prediction_window,
                        'officer_past_activity_window': officer_past_activity}
            log.info(tmp_info)
            temporal_info.append(tmp_info)
            test_end_date -= relativedelta(**update_window_deltas[update_window])

    return temporal_info

def generate_feature_dates(temporal_config):
    """
    This function returns a list of all the as of dates for generating
    features given the different combinations of the temporal configuration
    """    
    experiments_dates = generate_temporal_info(temporal_config)
    train_dates = [e['train_as_of_dates'] for e in experiments_dates]
    test_dates = [e['test_as_of_dates'] for e in experiments_dates]
    
    flatten_and_set = lambda l: set([item for sublist in l for item in sublist])
    
    as_of_dates = flatten_and_set(train_dates)
    as_of_dates.update(flatten_and_set(test_dates))
    
    return list(as_of_dates)


def generate_model_config( config ):
    models_sklearn = { 'RandomForest': 'sklearn.ensemble.RandomForestClassifier',
                       'ExtraTrees': 'sklearn.ensemble.ExtraTreesClassifier',
                       'AdaBoost': 'sklearn.ensemble.AdaBoostClassifier',
                       'LogisticRegression': 'sklearn.linear_model.LogisticRegression',
                       'SVM': 'sklearn.svm.SVC',
                       'GradientBoostingClassifier': 'sklearn.ensemble.GradientBoostingClassifier',
                       'DecisionTreeClassifier': 'sklearn.tree.DecisionTreeClassifier',
                       'SGDClassifier': 'sklearn.linear_model.SGDClassifier',
                       'KNeighborsClassifier': 'sklearn.neighbors.KNeighborsClassifier'
                      }

    model_config = {}
    models = config['model']
    for model in models:
        model_config[models_sklearn[model]] = config['parameters'][model]
   
    return model_config


if __name__ == '__main__':
    config_file_name = 'example_officer_config.yaml'
    config_file = read_config(config_file_name)
    temporal_sets = generate_temporal_info(config_file['temporal_info'])
