#!/usr/bin/env python
import pdb
import logging
import sys
import yaml
import datetime
import sqlalchemy
from enum import Enum
import sqlalchemy.sql.expression as ex

from .. import setup_environment
from . import abstract

from collate import collate


log = logging.getLogger(__name__)
try:
    _, tables = setup_environment.get_database()
except:
    pass

time_format = "%Y-%m-%d %X"

# magic loops for generating certain conditions
class AllegationSeverity(Enum):
    major = "grouped_incident_type_code in ( 0, 2, 3, 4, 8, 9, 10, 11, 17, 20 )"
    minor = "grouped_incident_type_code in ( 1, 6, 16, 18, 12, 7, 14 )"
    unknown = ""

class AllegationOutcome(Enum):
    sustained = "final_ruling_code in (1, 4, 5 )"
    unsustained = ""
    unknown = "final_ruling_code = 0"

# Super class for feature generation
class FeaturesBlock():
    def __init__(self, **kwargs):

        self.lookback_durations = kwargs["lookback_durations"]
        self.unit_id = ""
        self.from_obj = ""
        self.date_column = ""
        self.prefix = ""
        self.suffix = ""

    def _lookup_values_conditions(self, engine, column_code_name, lookup_table, fix_condition ='', prefix=''):
        query = """select code, value from staging.{0}""".format(lookup_table)
        lookup_values = engine.connect().execute(query)
        dict_temp = {}
        for code,value in lookup_values:
            if fix_condition:
                 dict_temp[prefix + '_' + value]= "({0} = {1} AND {2})::int".format(column_code_name,
                                                                                    code,
                                                                                    fix_condition)
            else:
                dict_temp[prefix + '_' + value]= "({0} = {1})::int".format(column_code_name, code)
        return dict_temp

    def feature_aggregations_to_use(self, feature_list, engine):
        feature_aggregations = self._feature_aggregations(engine)
        feature_aggregations_to_use = []
        log.debug(feature_list)
        for feature in feature_list:
            try:
                feature_aggregations_to_use.append(feature_aggregations[feature])
            except KeyError:
                log.info("WARNING: no feature aggregation for feature: {}".format(feature))
                sys.exit(1)
        return feature_aggregations_to_use

    def _feature_aggregations(self, engine):
        return {}

    def build_collate(self, engine, as_of_dates,  feature_list):
        feature_aggregations_list = self.feature_aggregations_to_use(feature_list, engine)
        st = collate.SpacetimeAggregation(feature_aggregations_list,
                      from_obj = self.from_obj,
                      group_intervals = {self.unit_id: self.lookback_durations},
                      dates = as_of_dates,
                      date_column = self.date_column,
                      prefix = self.prefix)
        log.debug('Inserting {}'.format(self.prefix))
        st.execute(engine.connect())

#--------------------------
# REPORTED INCIDENTS: 
# Only considers when the incident is reported not the outcome
#-------------------------
class IncidentsReported(FeaturesBlock):
    def __init__(self, **kwargs):
        FeaturesBlock.__init__(self, **kwargs)
        self.unit_id = ex.text('officer_id')
        self.from_obj = ex.text('staging.incidents')
        self.date_column = "report_date"
        self.lookback_durations = kwargs["lookback_durations"]
        self.prefix = 'incidents_reported'

    def _feature_aggregations(self, engine):
        return {
        'InterventionsOfType': collate.Aggregate(
                   self._lookup_values_conditions(engine, column_code_name = 'intervention_type_code',
                                                          lookup_table = 'lookup_intervention_types',
                                                          prefix = 'InterventionsOfType'), ['count']),

        'IncidentsOfType': collate.Aggregate(
                   self._lookup_values_conditions(engine, column_code_name = 'grouped_incident_type_code',
                                                          lookup_table = 'lookup_incident_types',
                                                          prefix = 'IncidentsOfType'), ['count']),

        'ComplaintsTypeSource': collate.Aggregate(
                   self._lookup_values_conditions(engine, column_code_name = 'origination_type_code',
                                                          lookup_table = 'lookup_complaint_origins',
                                                          prefix = 'ComplaintsTypeSource'), ['count']),

        'SuspensionsOfType': collate.Aggregate(
                   { "SuspensionsOfType_active": "(hours_active_suspension > 0)::int",
                     "SuspensionsOfType_inactive": "(hours_inactive_suspension > 0)::int" },['count']),

        'HoursSuspensionsOfType':collate.Aggregate(
                   { "HoursSuspensionsOfType_active": "hours_active_suspension",
                     "HoursSuspensionsOfType_inactive": "hours_inactive_suspension" }, ['sum']),

        'AllAllegations': collate.Aggregate(
                   { "AllAllegations": "number_of_allegations"}, ['sum']),

        'IncidentsOfSeverity': collate.Aggregate(
                   { "IncidentsOfSeverity_major": "({})::int".format(AllegationSeverity['major'].value),
                     "IncidentsOfSeverity_minor": "({})::int".format(AllegationSeverity['minor'].value) },['count']),

        'IncidentsSeverityUnknown': collate.Aggregate(
                   { "IncidentsSeverityUnknown_major": "({0} and {1})::int".format(
                         AllegationSeverity['major'].value,  AllegationOutcome['unknown'].value),
                     "IncidentsSeverityUnknown_minor": "({} and {})::int".format(
                         AllegationSeverity['minor'].value, AllegationOutcome['unknown'].value)},['count']),

        'Complaints': collate.Aggregate(
                   {"Complaints": "(origination_type_code is not null)::int"}, ['count']),
       
        'DaysSinceLastAllegation': collate.Aggregate(
                   {"DaysSinceLastAllegation": "{date} - report_date"}, ['min'])
                        
        } 

# --------------------------------------------------------
# BLOCK: COMPLETED INCIDENTS
# Consider the outcome of the incident
# -------------------------------------------------------
class IncidentsCompleted(FeaturesBlock):
    def __init__(self, **kwargs):
        FeaturesBlock.__init__(self, **kwargs)
        self.unit_id = ex.text('officer_id')
        self.from_obj = ex.text('staging.incidents')
        self.date_column = 'date_of_judgment'
        self.lookback_durations = kwargs["lookback_durations"]
        self.prefix = 'incidents_completed'

    def _feature_aggregations(self, engine):
        return {
        'IncidentsByOutcome': collate.Aggregate(
                  self._lookup_values_conditions(engine, column_code_name = 'final_ruling_code',
                                                         lookup_table = 'lookup_final_rulings',
                                                         prefix = 'IncidentsByOutcome'),['count']),
        
       'MajorIncidentsByOutcome': collate.Aggregate(
                  self._lookup_values_conditions(engine, column_code_name = 'final_ruling_code',
                                                         lookup_table = 'lookup_final_rulings',
                                                         fix_condition = AllegationSeverity['major'].value,
                                                         prefix = 'MajorIncidentsByOutcome'),['count']),
        
        'MinorIncidentsByOutcome': collate.Aggregate(
                  self._lookup_values_conditions(engine, column_code_name = 'final_ruling_code',
                                                         lookup_table = 'lookup_final_rulings',
                                                         fix_condition = AllegationSeverity['minor'].value,
                                                         prefix = 'MinorIncidentsByOutcome'), ['count']),
 
        'DaysSinceLastSustainedAllegation': collate.Aggregate(
                  {"DaysSinceLastSustainedAllegation": "{} - date_of_judgment"}, ['min'])
            }

# --------------------------------------------------------
# BLOCK: SHIFTS
# --------------------------------------------------------
class OfficerShifts(FeaturesBlock):
    def __init__(self, **kwargs):
        FeaturesBlock.__init__(self, **kwargs)
        self.unit_id = 'officer_id'
        self.from_obj = 'staging.officer_shifts'
        self.date_column = 'stop_datetime'
        self.prefix = 'shifts'

    def _feature_aggregations(self, engine):
        return {
        'ShiftsOfType': collate.Aggregate(
                  self._lookup_values_conditions(engine, column_code_name = 'shift_type_code',
                                                         lookup_table = 'lookup_shift_types',
                                                         prefix = 'ShiftsOfType'),['count']),
        
        'MeanHoursPerShift': collate.Aggregate(
                  {'MeanHoursPerShift': '(EXTRACT( EPOCH from shift_length)/3600)'}, ['avg'])
            }

# --------------------------------------------------------
# BLOCK: ARRESTS
# --------------------------------------------------------
class OfficerArrests(FeaturesBlock):
    def __init__(self, **kwargs):
        FeaturesBlock.__init__(self, **kwargs)
        self.unit_id = 'officer_id'
        self.from_obj = 'staging.arrests'
        self.date_column = 'event_datetime'
        self.prefix = 'arrests'

    def _feature_aggregations(self, engine):
        return {
        'Arrests': collate.Aggregate(
                  {"Arrests": 'event_id'}, ['count']),

        'ArrestsOfType': collate.Aggregate(
                  self._lookup_values_conditions(engine, column_code_name = 'arrest_type_code',
                                                         lookup_table = 'lookup_arrest_types',
                                                         prefix = 'ArrestsOfType'), ['count']),

        'ArrestsON': collate.Aggregate(
                  self._lookup_values_conditions(engine, column_code_name = 'arrest_day_of_week',
                                                         lookup_table = 'lookup_days_of_week',
                                                         prefix = 'ArrestsON'), ['count']),

        'SuspectsArrestedOfRace': collate.Aggregate(
                  self._lookup_values_conditions(engine, column_code_name = 'suspect_race_code',
                                                         lookup_table = 'lookup_races',
                                                         prefix = 'SuspectsArrestedOfRace'), ['count']),
        'SuspectsArrestedOfEthnicity': collate.Aggregate(
                  self._lookup_values_conditions(engine, column_code_name = 'suspect_ethnicity_code',
                                                         lookup_table = 'lookup_ethnicities',
                                                         prefix = 'SuspectsArrestedOfEthnicity'), ['count'])
         }

# --------------------------------------------------------
# BLOCK: TRAFFIC STOPS
# --------------------------------------------------------
class TrafficStops(FeaturesBlock):
    def __init__(self, **kwargs):
        FeaturesBlock.__init__(self, **kwargs)
        self.unit_id = 'officer_id'
        self.from_obj = 'staging.traffic_stops'
        self.date_column = 'event_datetime'
        self.prefix = 'traffic_stops'

    def _feature_aggregations(self, engine):
        return {
        'TrafficStopsWithSearch': collate.Aggregate(
                 {"TrafficStopsWithSearch": '(searched_flag = true)::int'}, ['count']),

        'TrafficStopsWithUseOfForce': collate.Aggregate(
                 {"TrafficStopsWithUseOfForce": '(use_of_force_flag = true)::int'}, ['count']),

        'TrafficStops': collate.Aggregate(
                 {"TrafficStops": 'event_id'}, ['count']),

        'TrafficStopsWithArrest': collate.Aggregate(
                 {"TrafficStopsWithArrest": '(arrest_flag = true)::int'}, ['count']),

        'TrafficStopsWithInjury': collate.Aggregate(
                 {"TrafficStopsWithInjury": '(injuries_flag = true)::int'}, ['count']),

        'TrafficStopsWithOfficerInjury': collate.Aggregate(
                 {"TrafficStopsWithOfficerInjury": '(officer_injury_flag=true)::int'}, ['count']),

        'TrafficStopsWithSearchRequest': collate.Aggregate(
                 {"TrafficStopsWithSearchRequest": 'search_consent_request_flag::int'}, ['sum','avg']),

        'TrafficStopsByRace': collate.Aggregate(
                 self._lookup_values_conditions(engine, column_code_name = 'stopped_person_race_code',
                                                        lookup_table = 'lookup_races',
                                                        prefix = 'TrafficStopsByRace'), ['count']),

        'TrafficStopsByStopType': collate.Aggregate(
                 self._lookup_values_conditions(engine, column_code_name = 'stop_type_code',
                                                        lookup_table = 'lookup_traffic_stop_type',
                                                        prefix = 'TrafficStopsByStopType'), ['count']),

        'TrafficStopsByStopResult': collate.Aggregate(
                 self._lookup_values_conditions(engine, column_code_name = 'stop_outcome_code',
                                                        lookup_table = 'lookup_traffic_stop_outcome_type',
                                                        prefix = 'TrafficStopsByStopResult'), ['count']),

        'TrafficStopsBySearchReason': collate.Aggregate(
                 self._lookup_values_conditions(engine, column_code_name = 'search_justification_code',
                                                        lookup_table = 'lookup_search_justifications',
                                                        prefix = 'TrafficStopsBySearchReason'), ['count'])
               }


# --------------------------------------------------------
# BLOCK: FIELD INTERVIEWS
# --------------------------------------------------------
class FieldInterviews(FeaturesBlock):
    def __init__(self, **kwargs):
        FeaturesBlock.__init__(self, **kwargs)
        self.unit_id = 'officer_id'
        self.from_obj = 'staging.field_interviews'
        self.date_column = 'event_datetime'
        self.prefix = 'field_interviews'

    def _feature_aggregations(self, engine):
        return {
        'FieldInterviews': collate.Aggregate(
                { "FieldInterviews": 'event_id'}, ['sum']),

        'HourOfFieldInterviews': collate.Aggregate(
                { "HourOfFieldInterviews": "date_part('hour',event_datetime)-12"}, ['avg']),

        'FieldInterviewsByRace': collate.Aggregate(
                self.lookup_values_conditions(engine, column_code_name = 'interviewed_person_race',
                                                      lookup_table = 'lookup_races',
                                                      prefix = 'FieldInterviewsByRace'), ['sum', 'avg']),

        'FieldInterviewsByOutcome': collate.Aggregate(
                self.lookup_values_conditions(engine, column_code_name = 'field_interview_outcome_code',
                                                      lookup_table = 'lookup_field_interview_outcomes',
                                                      prefix = 'FieldInterviewsByOutcome'), ['sum', 'avg']),

        'FieldInterviewsWithFlag': collate.Aggregate(
                { "FieldInterviewsWithFlag_searched": 'searched_flag',
                  "FieldInterviewsWithFlag_drugs": 'drugs_found_flag',
                  "FieldInterviewsWithFlag_weapons": 'weapons_found_flag'}, ['sum', 'avg']),

        'InterviewsType': collate.Aggregate(
                self.lookup_values_conditions(engine, column_code_name = 'field_interview_type_code',
                                                      lookup_table = 'lookup_field_interview_types',
                                                      prefix = 'InterviewsType'), ['sum'])
               }

