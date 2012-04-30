Time-series API implementation in Django
========================================

This is an implementation of the OpenOrg `time-series API specification
<http://openorg.ecs.soton.ac.uk/wiki/Metering>`_ as a Django application.


Features
--------

* Stores data in a compact binary format for quick retrieval
* Archives data in CSV format to negate format-based lock-in
* Time-zone aware
* Customisable aggregation (e.g. for daily and weekly averages, minima and maxima)
* Implements an API used by other time-series implementations
* Allows creation, modification and updating of time-series from a RESTful web service
* Has a fine-grained permissions model for administering time-series


Features yet to be implemented
------------------------------

* Administration interface is still somewhat human-unfriendly
* Customisable alerts for when series haven't been updated for some period of time
* Gauge and counter-based series (currently only period-based series)
* Virtual time-series (i.e. time-series which are some function of other time-series)
 

Architecture
------------


This project comprises a Django application that you can include in an existing
Django project by adding ``'openorg_timeseries'`` to your ``INSTALLED_APPS``
variable in your Django settings file.

``openorg_timeseries.longliving`` contains a ``threading.Thread`` which mediates access to the underlying data, and which prevents ...

Demonstration application
-------------------------

This project comes with a demonstration web service which you can use to quickly evaluate its usefulness.

Running
~~~~~~~

First, install the necessary dependencies using pip:

    $ pip install -r requirements.txt

Next, start the demonstration server using the following:

    $ django-admin.py rundemo --settings=openorg_timeseries.demo.settings --pythonpath=.

Give it a few seconds, after which you can point a web browser at `http://localhost:8000/ <http://localhost:8000/` to see it in action.


Details
~~~~~~~

The demo site performs the following steps on start-up:

#. Creates a ``demo-data`` directory in the current directory to store data used by the demo
#. Runs the ``syncdb`` Django management command to create a ``sqlite3`` database in the ``demo-data`` directory
#. Starts a long-living process which manages the data storage and retrieval
#. Creates a new time-series and loads in some example data
#. Runs the ``runserver`` management command (without the auto-reloader) to start the Django development server

