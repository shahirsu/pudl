"""A module for extracting the FERC Form 1 FoxPro Database for use in PUDL."""
import logging
import os.path
import re
import string

import dbfread
import pandas as pd
import sqlalchemy as sa

import pudl
import pudl.constants as pc
import pudl.datastore.datastore as datastore

logger = logging.getLogger(__name__)


###########################################################################
# Functions for cloning the FoxPor DB into SQLite
###########################################################################
def connect_db(pudl_settings=None, testing=False):
    """
    Create an SQL Alchemy engine for the FERC Form 1 SQLite database.

    By convention we have been storing two versions of this database:
      * for testing: PUDL_OUTDIR/sqlite/ferc1_test.sqlite
      * for live use: PUDL_OUTDIR/sqlite/ferc1.sqlite

    Args:
        sqlite_file (str): A string representation of the absolute path to the
            FERC Form 1 SQLite database file you want to access or create.

    Returns:
        sa.engine: An SQL Alchemy database engine.

    """
    if pudl_settings is None:
        pudl_settings = pudl.settings.init()

    if testing:
        return sa.create_engine(pudl_settings['ferc1_test_sqlite_url'])

    return sa.create_engine(pudl_settings['ferc1_sqlite_url'])


def _create_tables(engine, md):
    """Creates the FERC Form 1 DB tables."""
    md.create_all(engine)


def drop_tables(engine):
    """Drops the FERC Form 1 SQLite database."""
    md = sa.MetaData(bind=engine)
    md.reflect(engine)
    md.drop_all(engine)
    conn = engine.connect()
    conn.execute("VACUUM")
    conn.close()


def add_sqlite_table(table_name, sqlite_meta, dbc_map, data_dir,
                     refyear=max(pc.working_years['ferc1']),
                     bad_cols=()):
    """Creates a new Table to receive FERC Form 1 data.

    Args:
        table_name ():
        sqlite_meta ():
        dbc_map ():
        bad_cols ():

    Todo:
        Zane revisit

    """
    # Create the new table object
    new_table = sa.Table(table_name, sqlite_meta)
    ferc1_dbf = dbfread.DBF(
        get_dbf_path(table_name, refyear, data_dir=data_dir))

    # Add Columns to the table
    for field in ferc1_dbf.fields:
        if field.name == '_NullFlags':
            continue
        col_name = dbc_map[table_name][field.name]
        if (table_name, col_name) in bad_cols:
            continue
        col_type = pc.dbf_typemap[field.type]
        if col_type == sa.String:
            col_type = sa.String(length=field.length)
        new_table.append_column(sa.Column(col_name, col_type))

    col_names = [c.name for c in new_table.columns]

    if table_name == 'f1_respondent_id':
        new_table.append_constraint(
            sa.PrimaryKeyConstraint(
                'respondent_id', sqlite_on_conflict='REPLACE'
            )
        )

    if (('respondent_id' in col_names) and (table_name != 'f1_respondent_id')):
        new_table.append_constraint(
            sa.ForeignKeyConstraint(
                columns=['respondent_id', ],
                refcolumns=['f1_respondent_id.respondent_id']
            )
        )


def dbc_filename(year, data_dir):
    """Given a year, returns the path to the master FERC Form 1 .DBC file.

    Args:
        year (int): The year that we're trying to read data for

    Returns:
        str: the file path to the master FERC Form 1 .DBC file for the year
    """
    ferc1_path = datastore.path('ferc1', data_dir=data_dir,
                                year=year, file=False)
    return os.path.join(ferc1_path, 'F1_PUB.DBC')


def get_strings(filename, min_length=4):
    """
    Extract printable strings from a binary and return them as a generator.

    This is meant to emulate the Unix "strings" command, for the purposes of
    grabbing database table and column names from the F1_PUB.DBC file that is
    distributed with the FERC Form 1 data.

    Args:
        filename (str): the name of the DBC file from which to extract strings
        min_length (int): the minimum number of consecutive printable
            characters that should be considered a meaningful string and
            extracted.

    Yields:
        str: result

    Todo:
        Zane revisit

    """
    with open(filename, errors="ignore") as f:
        result = ""
        for c in f.read():
            if c in string.printable:
                result += c
                continue
            if len(result) >= min_length:
                yield result
            result = ""
        if len(result) >= min_length:  # catch result at EOF
            yield result


def get_dbc_map(year, data_dir, min_length=4):
    """Extracts the names of all the tables and fields from FERC Form 1 DB.

    This function reads all the strings in the given DBC database file for the
    and picks out the ones that appear to be database table names, and their
    subsequent table field names, for use in re-naming the truncated columns
    extracted from the corresponding DBF files (which are limited to having
    only 10 characters in their names.) Strings must have at least min_length
    printable characters.

    TODO: This routine shouldn't refer to any particular year of data, but
    right now it depends on the ferc1_dbf2tbl dictionary, which was generated
    from the 2015 Form 1 database.

    For more info see: https://github.com/catalyst-cooperative/pudl/issues/288

    Args:
        year (int): The year of data from which the database table and column
            names are to be extracted. Typically this is the most recently
            available year of FERC Form 1 data.
        min_length (int): The minimum number of consecutive printable
            characters that should be considered a meaningful string and
            extracted.

    Returns:
        dict: a dictionary whose keys are the long table names extracted
            from the DBC file, and whose values are lists of pairs of values,
            the first of which is the full name of each field in the table with
            the same name as the key, and the second of which is the truncated
            (<=10 character) long name of that field as found in the DBF file.
    """
    # Extract all the strings longer than "min" from the DBC file
    dbc_strings = list(
        get_strings(dbc_filename(year, data_dir), min_length=min_length)
    )

    # Get rid of leading & trailing whitespace in the strings:
    dbc_strings = [s.strip() for s in dbc_strings]

    # Get rid of all the empty strings:
    dbc_strings = [s for s in dbc_strings if s != '']

    # Collapse all whitespace to a single space:
    dbc_strings = [re.sub(r'\s+', ' ', s) for s in dbc_strings]

    # Pull out only strings that begin with Table or Field
    dbc_strings = [s for s in dbc_strings if re.match('(^Table|^Field)', s)]

    # Split strings by whitespace, and retain only the first two elements.
    # This eliminates some weird dangling junk characters
    dbc_strings = [' '.join(s.split()[:2]) for s in dbc_strings]

    # Remove all of the leading Field keywords
    dbc_strings = [re.sub('Field ', '', s) for s in dbc_strings]

    # Join all the strings together (separated by spaces) and then split the
    # big string on Table, so each string is now a table name followed by the
    # associated field names, separated by spaces
    dbc_table_strings = ' '.join(dbc_strings).split('Table ')

    # strip leading & trailing whitespace from the lists
    # and get rid of empty strings:
    dbc_table_strings = [s.strip() for s in dbc_table_strings if s != '']

    # Create a dictionary using the first element of these strings (the table
    # name) as the key, and the list of field names as the values, and return
    # it:
    tf_dict = {}
    for table_string in dbc_table_strings:
        table_and_fields = table_string.split()
        tf_dict[table_and_fields[0]] = table_and_fields[1:]

    dbc_map = {}
    for table in pc.ferc1_tbl2dbf:
        dbf_path = get_dbf_path(table, year, data_dir=data_dir)
        if os.path.isfile(dbf_path):
            dbf_fields = dbfread.DBF(dbf_path).field_names
            dbf_fields = [f for f in dbf_fields if f != '_NullFlags']
            dbc_map[table] = \
                {k: v for k, v in zip(dbf_fields, tf_dict[table])}
            assert len(tf_dict[table]) == len(dbf_fields)

    # Insofar as we are able, make sure that the fields match each other
    for k in dbc_map:
        for sn, ln in zip(dbc_map[k].keys(), dbc_map[k].values()):
            assert ln[:8] == sn.lower()[:8]

    return dbc_map


def define_sqlite_db(sqlite_meta, dbc_map, data_dir,
                     tables=pc.ferc1_tbl2dbf,
                     refyear=max(pc.working_years['ferc1']),
                     bad_cols=()):
    """Defines a FERC Form 1 DB structure in a given SQLAlchemy MetaData object.

    Given a template from an existing year of FERC data, and a list of target
    tables to be cloned, convert that information into table and column names,
    and data types, stored within a SQLAlchemy MetaData object. Use that
    MetaData object (which is bound to the SQLite database) to create all the
    tables to be populated later.

    Args:
        sqlite_meta (sa.MetaData): A SQLAlchemy MetaData object which is bound
            to the FERC Form 1 SQLite database.
        dbc_map (dict of dicts): A dictionary of dictionaries, of the kind
            returned by get_dbc_map(), describing the table and column names
            stored within the FERC Form 1 FoxPro database files.
        tables (iterable of strings): List or other iterable of FERC database
            table names that should be included in the database being defined.
            e.g. 'f1_fuel' and 'f1_steam'
        refyear (integer): The year of the FERC Form 1 DB to use as a template
            for creating the overall multi-year database schema.
        bad_cols (iterable of 2-tuples): A list or other iterable containing
            pairs of strings of the form (table_name, column_name), indicating
            columns (and their parent tables) which should *not* be cloned
            into the SQLite database for some reason.

    Returns:
        None: the effects of the function are stored inside sqlite_meta
    """
    for table in tables:
        add_sqlite_table(table, sqlite_meta, dbc_map,
                         refyear=refyear,
                         bad_cols=bad_cols,
                         data_dir=data_dir)

    sqlite_meta.create_all()


def get_dbf_path(table, year, data_dir):
    """Given a year and table name, returns the path to its datastore DBF file.

    Args:
        table (string): The name of one of the FERC Form 1 data tables. For
            example 'f1_fuel' or 'f1_steam'
        year (int): The year whose data you wish to find.

    Returns:
        str: dbf_path, a (hopefully) OS independent path including the
            filename of the DBF file corresponding to the requested year and
            table name.
    """
    dbf_name = pc.ferc1_tbl2dbf[table]
    ferc1_dir = datastore.path(
        'ferc1', year=year, file=False, data_dir=data_dir)
    dbf_path = os.path.join(ferc1_dir, f"{dbf_name}.DBF")
    return dbf_path


class FERC1FieldParser(dbfread.FieldParser):
    """A custom DBF parser to deal with bad FERC Form 1 data types."""

    def parseN(self, field, data):
        """Augments the Numeric DBF parser to account for bad FERC data.

        There are a small number of bad entries in the backlog of FERC Form 1
        data. They take the form of leading/trailing zeroes or null characters
        in supposedly numeric fields, and occasionally a naked '.'

        Accordingly, this custom parser strips leading and trailing zeros and
        null characters, and replaces a bare '.' character with zero, allowing
        all these fields to be cast to numeric values.

        Args:
            self ():
            field ():
            data ():

        Todo: Zane revisit
        """
        # Strip whitespace, null characters, and zeroes
        data = data.strip().strip(b'*\x00').strip(b'0')
        # Replace bare periods (which are non-numeric) with zero.
        if data == b'.':
            data = b'0'
        return super(FERC1FieldParser, self).parseN(field, data)


def get_raw_df(table, dbc_map, data_dir,
               years=pc.data_years['ferc1']):
    """Combines several years of a given FERC Form 1 DBF table into a dataframe.

    Args:
        table (string): The name of the FERC Form 1 table from which data is
            read.
        dbc_map (dict of dicts): A dictionary of dictionaries, of the kind
            returned by get_dbc_map(), describing the table and column names
            stored within the FERC Form 1 FoxPro database files.
        years (list): The range of years to be combined into a single DataFrame.

    Returns:
        pandas.DataFrame: A DataFrame containing several years of FERC Form 1
            data for the given table.
    """
    dbf_name = pc.ferc1_tbl2dbf[table]

    raw_dfs = []
    for yr in years:
        ferc1_dir = datastore.path(
            'ferc1', year=yr, file=False, data_dir=data_dir)
        dbf_path = os.path.join(ferc1_dir, f"{dbf_name}.DBF")

        if os.path.exists(dbf_path):
            new_df = pd.DataFrame(
                iter(dbfread.DBF(dbf_path,
                                 encoding='latin1',
                                 parserclass=FERC1FieldParser)))
            raw_dfs = raw_dfs + [new_df, ]

    if raw_dfs:
        return (
            pd.concat(raw_dfs, sort=True).
            drop('_NullFlags', axis=1, errors='ignore').
            rename(dbc_map[table], axis=1)
        )


def dbf2sqlite(tables=pc.ferc1_tbl2dbf,
               years=pc.data_years['ferc1'],
               refyear=max(pc.working_years['ferc1']),
               pudl_settings=None,
               testing=False,
               bad_cols=()):
    """
    Clone the FERC Form 1 Databsae to SQLite

    Args:
        testing (bool): Determines whether the live or testing database is
            used, changing which file the data is stored in. If present, the
            old database will be dropped before the new one is loaded.
        tables (iterable): What tables should be cloned?
        years (iterable): Which years of data should be cloned?
        refyear (int): Which database year to use as a template.
        bad_cols (iterable of tuples): A list of (table, column) pairs
            indicating columns that should be skipped during the cloning
            process. Both table and column are strings in this case, the
            names of their respective entities within the database metadata.

    Returns:
        None

    """
    if pudl_settings is None:
        pudl_settings = pudl.settings.init()
    # Read in the structure of the DB, if it exists
    logger.info("Dropping the old FERC Form 1 SQLite DB if it exists.")
    sqlite_engine = connect_db(pudl_settings=pudl_settings,
                               testing=testing)
    try:
        # So that we can wipe it out
        drop_tables(sqlite_engine)
    except sa.exc.OperationalError:
        pass

    # And start anew
    sqlite_engine = connect_db(pudl_settings=pudl_settings,
                               testing=testing)
    sqlite_meta = sa.MetaData(bind=sqlite_engine)

    # Get the mapping of filenames to table names and fields
    logger.info(f"Creating a new database schema based on {refyear}.")
    dbc_map = get_dbc_map(refyear, data_dir=pudl_settings['data_dir'])
    define_sqlite_db(sqlite_meta, dbc_map, tables=tables,
                     refyear=refyear, bad_cols=bad_cols,
                     data_dir=pudl_settings['data_dir'])

    for table in tables:
        logger.info(f"Pandas: reading {table} into a DataFrame.")
        new_df = get_raw_df(table, dbc_map, years=years,
                            data_dir=pudl_settings['data_dir'])
        # Because this table has no year in it, there would be multiple
        # definitions of respondents if we didn't drop duplicates.
        if table == 'f1_respondent_id':
            new_df = new_df.drop_duplicates(
                subset='respondent_id', keep='last')
        n_recs = len(new_df)
        logger.debug(f"    {table}: N = {n_recs}")
        # Only try and load the table if there are some actual records:
        if n_recs <= 0:
            continue

        # Write the records out to the SQLite database, and make sure that
        # the inferred data types are being enforced during loading.
        # if_exists='append' is being used because we defined the tables
        # above, but left them empty. Becaue the DB is reset at the beginning
        # of the function, this shouldn't ever result in duplicate records.
        coltypes = {col.name: col.type for col in sqlite_meta.tables[table].c}
        logger.info(f"SQLite: loading {n_recs} rows into {table}.")
        new_df.to_sql(table, sqlite_engine,
                      if_exists='append', chunksize=100000,
                      dtype=coltypes, index=False)


###########################################################################
# Functions for extracting ferc1 tables from SQLite to PUDL
###########################################################################


def extract(ferc1_tables=pc.ferc1_pudl_tables,
            ferc1_years=pc.working_years['ferc1'],
            pudl_settings=None, testing=False):
    """Coordinates the extraction of all FERC Form 1 tables into PUDL.

    Args:
        ferc1_tables (iterable of strings): List of the FERC 1 database tables
            to be loaded into PUDL. These are the names of the tables in the
            PUDL database, not the FERC Form 1 database.
        ferc1_years (iterable of ints): List of years for which FERC Form 1
            data should be loaded into PUDL. Note that not all years for which
            FERC data is available may have been integrated into PUDL yet.
        testing (bool): If True, use ferc1_test.sqlite to avoid clobbering a
            live FERC Form 1 database.

    Returns:
        ferc1_raw_dfs (dict of DataFrames): A dictionary of pandas DataFrames,
            with the names of PUDL database tables as the keys. These are the
            raw unprocessed dataframes, reflecting the data as it is in the
            FERC Form 1 DB, for passing off to the data tidying and cleaning
            fuctions found in the pudl.transform.ferc1 module.

    Raises:
        ValueError: If the year is not in the list of years for which FERC data
            is available
        ValueError: If the year is not in the list of working FERC years
        ValueError: If the FERC table requested is not integrated into PUDL
        AssertionError: If no ferc1_meta tables are found

    """
    if (not ferc1_tables) or (not ferc1_years):
        return {}

    for year in ferc1_years:
        if year not in pc.data_years['ferc1']:
            raise ValueError(
                f"FERC Form 1 data from the year {year} was requested but is "
                f"not available. The years for which data is available are: "
                f"{' '.join(pc.data_years['ferc1'])}."
            )
        if year not in pc.working_years['ferc1']:
            raise ValueError(
                f"FERC Form 1 data from the year {year} was requested but it "
                f"has not yet been integrated into PUDL. "
                f"If you'd like to contribute the necessary cleaning "
                f"functions, come find us on GitHub: "
                f"{pudl.__downloadurl__}"
                f"For now, the years which PUDL has integrated are: "
                f"{' '.join(pc.working_years['ferc1'])}."
            )
    for table in ferc1_tables:
        if table not in pc.ferc1_pudl_tables:
            raise ValueError(
                f"FERC Form 1 table {table} was requested but it has not yet "
                f"been integreated into PUDL. Heck, it might not even exist! "
                f"If you'd like to contribute the necessary cleaning "
                f"functions, come find us on GitHub: "
                f"{pudl.__downloadurl__}"
                f"For now, the tables which PUDL has integrated are: "
                f"{' '.join(pc.ferc1_pudl_tables)}"
            )

    # Connect to the local SQLite DB and read its structure.
    ferc1_engine = connect_db(pudl_settings=pudl_settings,
                              testing=testing)
    ferc1_meta = sa.MetaData(bind=ferc1_engine)
    ferc1_meta.reflect()
    if not ferc1_meta.tables:
        raise AssertionError(
            f"No FERC Form 1 tables found. Is the SQLite DB initialized?"
        )

    ferc1_extract_functions = {
        'fuel_ferc1': fuel,
        'plants_steam_ferc1': plants_steam,
        'plants_small_ferc1': plants_small,
        'plants_hydro_ferc1': plants_hydro,
        'plants_pumped_storage_ferc1': plants_pumped_storage,
        'plant_in_service_ferc1': plant_in_service,
        'purchased_power_ferc1': purchased_power,
        'accumulated_depreciation_ferc1': accumulated_depreciation}

    ferc1_raw_dfs = {}
    for pudl_table in ferc1_tables:
        if pudl_table not in ferc1_extract_functions:
            raise ValueError(
                f"No extract function found for requested FERC Form 1 data "
                f"table {pudl_table}!"
            )
        ferc1_sqlite_table = pc.table_map_ferc1_pudl[pudl_table]
        logger.info(
            f"Converting extracted FERC Form 1 table {pudl_table} into a "
            f"pandas DataFrame.")
        ferc1_raw_dfs[pudl_table] = ferc1_extract_functions[pudl_table](
            ferc1_meta, ferc1_sqlite_table, ferc1_years)

    return ferc1_raw_dfs


def fuel(ferc1_meta, ferc1_table, ferc1_years):
    """Creates a DataFrame of f1_fuel table records with plant names, >0 fuel.

    Args:
        ferc1_meta (sa.MetaData):
        ferc1_table (str): The name of the FERC 1 database table to read, in
            this case, the f1_fuel table.
        ferc1_years (list): The range of years from which to read data.

    Returns:
        pandas.DataFrame: A DataFrame containing f1_fuel records that have
            plant_names and non-zero fuel amounts.
    """
    # Grab the f1_fuel SQLAlchemy Table object from the metadata object.
    f1_fuel = ferc1_meta.tables[ferc1_table]
    # Generate a SELECT statement that pulls all fields of the f1_fuel table,
    # but only gets records with plant names and non-zero fuel amounts:
    f1_fuel_select = (
        sa.sql.select([f1_fuel]).
        where(f1_fuel.c.fuel != '').
        where(f1_fuel.c.fuel_quantity > 0).
        where(f1_fuel.c.plant_name != '').
        where(f1_fuel.c.report_year.in_(ferc1_years)).
        where(f1_fuel.c.respondent_id.notin_(pc.missing_respondents_ferc1))
    )
    # Use the above SELECT to pull those records into a DataFrame:
    return pd.read_sql(f1_fuel_select, ferc1_meta.bind)


def plants_steam(ferc1_meta, ferc1_table, ferc1_years):
    """Creates a DataFrame of f1_steam records with plant names, capacities > 0.

    Args:
        ferc1_meta (sa.MetaData):
        ferc1_table (str): The name of the FERC 1 database table to read, in
            this case, the f1_steam table.
        ferc1_years (list): The range of years from which to read data.

    Returns:
        pandas.DataFrame: A DataFrame containing f1_steam records that have
            plant names and non-zero capacities.
    """
    f1_steam = ferc1_meta.tables[ferc1_table]
    f1_steam_select = (
        sa.sql.select([f1_steam]).
        where(f1_steam.c.tot_capacity > 0).
        where(f1_steam.c.plant_name != '').
        where(f1_steam.c.report_year.in_(ferc1_years)).
        where(f1_steam.c.respondent_id.notin_(pc.missing_respondents_ferc1))
    )

    return pd.read_sql(f1_steam_select, ferc1_meta.bind)


def plants_small(ferc1_meta, ferc1_table, ferc1_years):
    """Creates a DataFrame of f1_small for records with minimum data criteria.

    Args:
        ferc1_meta (sa.MetaData):
        ferc1_table (str): The name of the FERC 1 database table to read, in
            this case, the f1_small table.
        ferc1_years (list): The range of years from which to read data.

    Returns:
        pandas.DataFrame: A DataFrame containing f1_small records that have
            plant names and non zero demand, generation, operations,
            maintenance, and fuel costs.
    """
    from sqlalchemy import or_

    f1_small = ferc1_meta.tables[ferc1_table]
    f1_small_select = (
        sa.sql.select([f1_small, ]).
        where(f1_small.c.report_year.in_(ferc1_years)).
        where(f1_small.c.plant_name != '').
        where(f1_small.c.respondent_id.notin_(pc.missing_respondents_ferc1)).
        where(or_((f1_small.c.capacity_rating != 0),
                  (f1_small.c.net_demand != 0),
                  (f1_small.c.net_generation != 0),
                  (f1_small.c.plant_cost != 0),
                  (f1_small.c.plant_cost_mw != 0),
                  (f1_small.c.operation != 0),
                  (f1_small.c.expns_fuel != 0),
                  (f1_small.c.expns_maint != 0),
                  (f1_small.c.fuel_cost != 0)))
    )

    return pd.read_sql(f1_small_select, ferc1_meta.bind)


def plants_hydro(ferc1_meta, ferc1_table, ferc1_years):
    """Creates a DataFrame of f1_hydro for records that have plant names.

    Args:
        ferc1_meta (sa.MetaData):
        ferc1_table (str): The name of the FERC 1 database table to read, in
            this case, the f1_hydro table.
        ferc1_years (list): The range of years from which to read data.

    Returns:
        pandas.DataFrame: A DataFrame containing f1_hydro records that have
            plant names.
    """
    f1_hydro = ferc1_meta.tables[ferc1_table]

    f1_hydro_select = (
        sa.sql.select([f1_hydro]).
        where(f1_hydro.c.plant_name != '').
        where(f1_hydro.c.report_year.in_(ferc1_years)).
        where(f1_hydro.c.respondent_id.notin_(pc.missing_respondents_ferc1))
    )

    return pd.read_sql(f1_hydro_select, ferc1_meta.bind)


def plants_pumped_storage(ferc1_meta, ferc1_table, ferc1_years):
    """Creates a DataFrame of f1_plants_pumped_storage records with plant names.

    Args:
        ferc1_meta (sa.MetaData):
        ferc1_table (str): The name of the FERC 1 database table to read, in
            this case, the f1_plants_pumped_storage table.
        ferc1_years (list): The range of years from which to read data.

    Returns:
        pandas.DataFrame: A DataFrame containing f1_plants_pumped_storage
            records that have plant names.
    """
    f1_pumped_storage = ferc1_meta.tables[ferc1_table]

    # Removing the empty records.
    # This reduces the entries for 2015 from 272 records to 27.
    f1_pumped_storage_select = (
        sa.sql.select([f1_pumped_storage]).
        where(f1_pumped_storage.c.plant_name != '').
        where(f1_pumped_storage.c.report_year.in_(ferc1_years)).
        where(f1_pumped_storage.c.respondent_id.
              notin_(pc.missing_respondents_ferc1))
    )

    return pd.read_sql(f1_pumped_storage_select, ferc1_meta.bind)


def plant_in_service(ferc1_meta, ferc1_table, ferc1_years):
    """Creates a DataFrame of the fields of plant_in_service_ferc1.

    Args:
        ferc1_meta (sa.MetaData):
        ferc1_table (str): The name of the FERC 1 database table to read, in
            this case, the plant_in_service_ferc1 table.
        ferc1_years (list): The range of years from which to read data.

    Returns:
        pandas.DataFrame: A DataFrame containing all plant_in_service_ferc1
            records.
    """
    f1_plant_in_srvce = ferc1_meta.tables[ferc1_table]
    f1_plant_in_srvce_select = (
        sa.sql.select([f1_plant_in_srvce]).
        where(f1_plant_in_srvce.c.report_year.in_(ferc1_years)).
        # line_no mapping is invalid before 2007
        where(f1_plant_in_srvce.c.report_year >= 2007).
        where(f1_plant_in_srvce.c.respondent_id.
              notin_(pc.missing_respondents_ferc1))
    )

    return pd.read_sql(f1_plant_in_srvce_select, ferc1_meta.bind)


def purchased_power(ferc1_meta, ferc1_table, ferc1_years):
    """Creates a DataFrame the fields of purchased_power_ferc1.

    Args:
        ferc1_meta (sa.MetaData):
        ferc1_table (str): The name of the FERC 1 database table to read, in
            this case, the purchased_power_ferc1 table.
        ferc1_years (list): The range of years from which to read data.

    Returns:
        pandas.DataFrame: A DataFrame containing all purchased_power_ferc1
            records.
    """
    f1_purchased_pwr = ferc1_meta.tables[ferc1_table]
    f1_purchased_pwr_select = (
        sa.sql.select([f1_purchased_pwr]).
        where(f1_purchased_pwr.c.report_year.in_(ferc1_years)).
        where(f1_purchased_pwr.c.respondent_id.
              notin_(pc.missing_respondents_ferc1))
    )

    return pd.read_sql(f1_purchased_pwr_select, ferc1_meta.bind)


def accumulated_depreciation(ferc1_meta, ferc1_table, ferc1_years):
    """Creates a DataFrame of the fields of accumulated_depreciation_ferc1.

    Args:
        ferc1_meta (sa.MetaData):
        ferc1_table (str): The name of the FERC 1 database table to read, in
            this case, the accumulated_depreciation_ferc1.
        ferc1_years (list): The range of years from which to read data.

    Returns:
        pandas.DataFrame: A DataFrame containing all
            accumulated_depreciation_ferc1 records.
    """
    f1_accumdepr_prvsn = ferc1_meta.tables[ferc1_table]
    f1_accumdepr_prvsn_select = (
        sa.sql.select([f1_accumdepr_prvsn]).
        where(f1_accumdepr_prvsn.c.report_year.in_(ferc1_years)).
        where(f1_accumdepr_prvsn.c.respondent_id.
              notin_(pc.missing_respondents_ferc1))
    )

    return pd.read_sql(f1_accumdepr_prvsn_select, ferc1_meta.bind)


###########################################################################
# Helper functions for debugging the extract process...
###########################################################################


def show_dupes(table, dbc_map, years=pc.data_years['ferc1'],
               pk=['respondent_id', 'report_year', 'report_prd',
                   'row_number', 'spplmnt_num']):
    """
    Todo:
        Zane revisit.
    """
    print(f"{table}:")
    for yr in years:
        raw_df = get_raw_df(table, dbc_map, years=[yr, ])
        if not set(pk).difference(set(raw_df.columns)):
            n_dupes = len(raw_df) - len(raw_df.drop_duplicates(subset=pk))
            if n_dupes > 0:
                print(f"    {yr}: {n_dupes}")
    # return raw_df[raw_df.duplicated(subset=pk, keep=False)]