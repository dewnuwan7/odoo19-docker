# -*- coding: utf-8 -*-
{
    'name': "open_redis_ormcache_19",

    'summary': """
        store ormcache in redis""",

    'description': """
    ORM Cache Redis for Odoo 19

    Installation Instructions:

    1. Install the Python dependency:
       pip install redis

    2. Place open_redis_ormcache inside your Odoo addons path.

    3. In your odoo.conf file, add the following key-value pairs:
       [options]
       ormcache_redis_url = redis://localhost:6379/0
       ormcache_redis_expire = 604800  ; optional, TTL in seconds (default 7 days)

    4. Restart your Odoo server to apply the patch.

    5. Go to Apps, update the app list, and install the "Redis ORM Cache" module.

    6. On startup, you should see logs confirming the Redis connection.

    Multi-Server Setup (Master-Slave Configuration):

    1. Prepare more than two servers, one is the master, the others are slave(s)
    2. Modify the master odoo.conf file:
       ormcache_redis_url = redis://@redis-ip:6379/0  (recommended to use a separate Redis instance)
       max_cron_threads = x  (where x > 1)
    3. Modify all slave(s) odoo.conf file:
       ormcache_redis_url = redis://@redis-ip:6379/0  (same as master)
       max_cron_threads = 0
    4. Use an NFS directory as data directory for master and slave(s) servers:
       data_dir = /path/to/nfs/directory
    """,

    'author': "Roshan",

    'category': 'Technical Settings',
    'version': '19.0.1.0',
    'license': 'OPL-1',

    'depends': ['base'],
    "external_dependencies": {
        'python': ['redis'],
    },
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': False,

}
