// clear current data set and set more off
clear all
set more off
	
// Connect to J Drive for get_populations function
adopath + "/home/j/temp/central_comp/libraries/current/stata/"

// Set output directory
local outpath `3'

// Set country of interest (using iso3 code)
local location_id `1'

// Set gbd round of interest
local gbd_round `2'

// Get every GBD age group (5 year intervals... age_group_id = 2-21, 30-33)
local ages
forvalues i = 2/21 {
local ages "`ages' `i' "
}
foreach i in 30 31 32 33 {
local ages "`ages' `i' "
}

// Use get_draws function to generate results
get_draws, gbd_id_field("cause_id") gbd_id(294) location_ids(`location_id') age_group_ids(`ages') measure_ids(1) source(dalynator) status(best) gbd_round_id(`2') clear

// Output results to a csv file
outsheet using `outpath', comma replace

exit, STATA clear
