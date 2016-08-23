DROP SCHEMA IF EXISTS results CASCADE;
CREATE SCHEMA results;

-- model table containing each of the models run.
CREATE TABLE results.models(
    model_id                    									serial primary key,
    run_time                    									timestamp,
    batch_run_time              									timestamp,
    model_type                                                      text,
    model_parameters                                                json,
    model_comment                                                   text,
    batch_comment                                                   text,
    config 				        		                            json,
    pickle_file_path_name                                           text
);

-- predictions corresponding to each model.
CREATE TABLE results.predictions(
    model_id                    								int references results.models(model_id),
    unit_id                     								bigint,
    unit_score                  								numeric,
    label_value                 								int
);

-- evaluation table containing metrics for each of the models run.
CREATE TABLE results.evaluations(
    model_id                   	            int references results.models(model_id),
    metric				                    text,
    parameter	           		            text,
    value                                   numeric,
    comment						            text
);

-- data table for storing pickle blobs.
CREATE TABLE results.data(
    model_id                    									int references results.models(model_id),
    pickle_blob                 									bytea
);

