-- Runs once on first container start. The app DB gets PostGIS via the image's own init script;
-- this adds a separate database for the test suite.
CREATE DATABASE rideassist_test;
\connect rideassist_test
CREATE EXTENSION IF NOT EXISTS postgis;
