// Use home/J if running on the cluster, J: if running locally
if c(os) == "Unix" {
                global prefix "/home/j"
                set odbcmgr unixodbc
        }
        else if c(os) == "Windows" {
                global prefix "J:"
        }
// Connect to J Drive for shared function
adopath + "$prefix/WORK/10_gbd/00_library/functions"

// clear current data and set more off
clear all
set more off

// Set output directory
local outpath `2'

// Set country of interest (using iso3 code)
local location_id `1'

// Set GBD years of interest
local years 1990 1995 2000 2005 2010 2013 2015

// Set sexes of interest
local sex 1 2 3

// Get every GBD age group (5 year intervals... age_group_id = 2-21, 30-33)
local ages
forvalues i = 2/21 {
local ages "`ages' `i' "
}
foreach i in 30 31 32 33 {
local ages "`ages' `i' "
}

// Use get_populations function to generate results
get_populations, year_id(`years') location_id(`location_id') sex_id(`sex') age_group_id(`ages') include_names clear

// Output results to a csv file. Columns are age_group id, year_id, location_id, sex_id, and pop_scaled
outsheet using `outpath', comma replace

exit, STATA clear