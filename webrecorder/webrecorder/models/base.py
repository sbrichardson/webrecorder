from datetime import datetime
from webrecorder.utils import get_bool, get_new_id


# ============================================================================
class DupeNameException(Exception):
    pass


# ============================================================================
class RedisUniqueComponent(object):
    INT_KEYS = ('size', 'created_at', 'updated_at', 'recorded_at')

    INFO_KEY = None
    MY_TYPE = None

    ALL_KEYS = None

    OWNER_CLS = None

    def __init__(self, **kwargs):
        self.redis = kwargs['redis']
        self.my_id = kwargs.get('my_id', '')
        self.name = kwargs.get('name', self.my_id)
        self.access = kwargs['access']
        self.owner = None

        if self.my_id:
            self.info_key = self.INFO_KEY.format_map({self.MY_TYPE: self.my_id})
        else:
            self.info_key = None

        if kwargs.get('load'):
            self.load()
        else:
            self.data = {}
            self.loaded = False

    @property
    def size(self):
        return self.get_prop('size', force_type=int, default_val=0, force_update=True)

    def incr_key(self, key, value):
        val = self.redis.hincrby(self.info_key, key, value)
        self.data[key] = int(val)
        self.set_prop('updated_at', self._get_now())

    def incr_size(self, size):
        self.incr_key('size', size)

    def set_bool_prop(self, prop, value):
        self.set_prop(prop, self._from_bool(value))

    def get_bool_prop(self, prop, default_val=False):
        return get_bool(self.get_prop(prop, default_val=False))

    def is_public(self):
        return self.get_bool_prop('public')

    def set_public(self, value):
        self.set_bool_prop('public', value)

    def load(self):
        self.data = self.redis.hgetall(self.info_key)
        self._format_keys()
        self.loaded = True

    def _format_keys(self):
        for key in self.INT_KEYS:
            if key in self.data:
                self.data[key] = int(self.data[key])

    def _create_new_id(self):
        #self.my_id = self.redis.incr(self.COUNTER_KEY)
        self.my_id = self.get_new_id()
        self.info_key = self.INFO_KEY.format_map({self.MY_TYPE: self.my_id})
        return self.my_id

    def _get_now(self):
        return int(datetime.utcnow().timestamp())

    def _init_new(self, pi=None):
        now = self._get_now()

        self.data['created_at'] = now
        self.data['updated_at'] = now

        self.commit(pi)

    def commit(self, pi=None):
        pi = pi or self.redis
        pi.hmset(self.info_key, self.data)

    def serialize(self, include_duration=False):
        if not self.loaded:
            self.load()

        self.data['id'] = self.my_id

        created_at = self.data.get('created_at', 0)
        updated_at = self.data.get('updated_at', 0)

        self.data['timespan'] = updated_at - created_at

        if include_duration:
            recorded_at = self.data.get('recorded_at', 0)
            self.data['duration'] = recorded_at - created_at if recorded_at else 0

        self.data['created_at'] = self.to_iso_date(created_at)
        self.data['updated_at'] = self.to_iso_date(updated_at)

        return self.data

    def get_prop(self, attr, default_val=None, force_type=None, force_update=False):
        if not self.loaded:
            if force_update or attr not in self.data:
                self.data[attr] = self.redis.hget(self.info_key, attr) or default_val
                if force_type:
                    self.data[attr] = force_type(self.data[attr])

        return self.data.get(attr, default_val)

    def set_prop(self, attr, value):
        self.data[attr] = value
        self.redis.hset(self.info_key, attr, value)

        # auto-update updated_at
        if attr != 'updated_at':
            self.set_prop('updated_at', self._get_now())

    def __getitem__(self, name):
        return self.get_prop(name)

    def __setitem__(self, name, value):
        self.set_prop(name, value)

    def get(self, name, default_val=''):
        return self.get_prop(name, default_val)

    def delete_object(self):
        deleted = False

        all_keys = self.ALL_KEYS.format_map({self.MY_TYPE: self.my_id})

        for key in self.redis.scan_iter(all_keys, count=100):
            self.redis.delete(key)
            deleted = True

        return deleted

    def get_owner(self):
        if self.owner:
            return self.owner

        owner_id = self.get_prop('owner')
        if not owner_id:
            return None

        self.owner = self.OWNER_CLS(my_id=owner_id,
                                    redis=self.redis,
                                    access=self.access)

        return self.owner

    def __eq__(self, obj):
        return obj and (self.my_id == obj.my_id) and type(self) == type(obj)

    @classmethod
    def to_iso_date(cls, dt, no_T=False):
        try:
            dt = float(dt)
        except:
            return dt

        #return datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M:%S.%f')
        dt = datetime.fromtimestamp(dt).isoformat()
        if no_T:
            dt = dt.replace('T', ' ')
        return dt

    @classmethod
    def _from_bool(self, value):
        return '1' if value else '0'

    @classmethod
    def get_new_id(cls):
        return get_new_id()


# ============================================================================
class RedisNamedContainer(RedisUniqueComponent):
    COMP_KEY = ''

    def remove_object(self, obj):
        if not obj:
            return 0

        comp_map = self.get_comp_map()
        res = self.redis.hdel(comp_map, obj.name)

        self.incr_size(-obj.size)
        return res

    def reserve_obj_name(self, name, allow_dupe=False):
        dupe_count = 1
        orig_name = name

        comp_map = self.get_comp_map()

        while True:
            if self.redis.hsetnx(comp_map, name, 0) == 1:
                break

            if not allow_dupe:
                raise DupeNameException(name)

            dupe_count += 1
            dupe_str = str(dupe_count)
            name = orig_name + '-' + dupe_str

        return name

    def add_object(self, name, obj, owner=False):
        comp_map = self.get_comp_map()

        self.redis.hset(comp_map, name, obj.my_id)

        self.incr_size(obj.size)

        obj.name = name

        if owner:
            obj.owner = self
            obj['owner'] = self.my_id

    def get_comp_map(self):
        return self.COMP_KEY.format_map({self.MY_TYPE: self.my_id})

    def name_to_id(self, obj_name):
        comp_map = self.get_comp_map()

        return self.redis.hget(comp_map, obj_name)

    def rename(self, obj, new_name, new_cont=None, allow_dupe=False):
        new_cont = new_cont or self
        new_name = new_cont.reserve_obj_name(new_name, allow_dupe=allow_dupe)

        if not self.remove_object(obj):
            return None

        new_cont.add_object(new_name, obj, owner=True)
        return new_name

    def move(self, obj, new_container, allow_dupe=False):
        return self.rename(obj, obj.name, new_container, allow_dupe=allow_dupe)

    def num_objects(self):
        return int(self.redis.hlen(self.get_comp_map()))

    def get_objects(self, cls):
        all_objs = self.redis.hgetall(self.get_comp_map())
        obj_list = [cls(my_id=val,
                        name=name,
                        redis=self.redis,
                        access=self.access) for name, val in all_objs.items()]

        return obj_list

    def is_owner(self, owner):
        return self == owner


# ============================================================================
class RedisOrderedList(object):
    SCORE_UNIT = 1024

    def __init__(self, ordered_list_key, comp):
        self.ordered_list_key_templ = ordered_list_key
        self.comp = comp
        self.redis = comp.redis

    @property
    def _ordered_list_key(self):
        return self.ordered_list_key_templ.format_map({self.comp.MY_TYPE: self.comp.my_id})

    def get_ordered_objects(self, cls, load=True, start=0, end=-1):
        all_objs = self.redis.zrange(self._ordered_list_key, start, end)

        obj_list = []
        for val in all_objs:
            obj = cls(my_id=val,
                      redis=self.redis,
                      access=self.comp.access)

            obj.owner = self.comp
            if load:
                obj.load()

            obj_list.append(obj)

        return obj_list

    def insert_ordered_object(self, obj, before_obj, owner=True):
        key = self._ordered_list_key

        new_score = None
        before_score = None

        if before_obj:
            before_score = self.redis.zscore(key, before_obj.my_id)

        if before_score is not None:
            res = self.redis.zrevrangebyscore(key, '(' + str(before_score), 0, start=0, num=1, withscores=True)
            # insert before before_obj, possibly at the beginning
            after_score = res[0][1] if res else 0
            new_score = (before_score + after_score) / 2.0

        # insert at the end
        if new_score is None:
            res = self.redis.zrevrange(key, 0, 1, withscores=True)
            if len(res) == 0:
                new_score = self.SCORE_UNIT

            elif len(res) == 1:
                new_score = res[0][1] * 2.0

            elif len(res) == 2:
                new_score = res[0][1] * 2.0 - res[1][1]

        self.redis.zadd(key, new_score, obj.my_id)

        if owner:
            obj.owner = self.comp
            obj['owner'] = self.comp.my_id

    def contains_id(self, obj_id):
        return self.redis.zscore(self._ordered_list_key, obj_id) is not None

    def num_ordered_objects(self):
        return self.redis.zcard(self._ordered_list_key)

    def remove_ordered_object(self, obj):
        return self.redis.zrem(self._ordered_list_key, obj.my_id)

    def get_ordered_keys(self):
        return self.redis.zrange(self._ordered_list_key, 0, -1)

    def reorder_objects(self, new_order):
        key = self._ordered_list_key

        all_objs = self.redis.zrange(key, 0, -1)

        new_order_set = set(new_order)

        # new_order list contains dupes, invalid order
        if len(new_order_set) != len(new_order):
            return False

        # new order set doesn't match current set, invalid order
        if set(all_objs) != new_order_set:
            return False

        add_list = []
        score_count = self.SCORE_UNIT
        for obj in new_order:
            add_list.append(score_count)
            add_list.append(obj)
            score_count += self.SCORE_UNIT

        # TODO: add xx=True if supported in redis-py
        self.redis.zadd(key, *add_list)
        return True


# ============================================================================
class RedisUnorderedList(object):
    def __init__(self, list_key, comp):
        self.list_key_templ = list_key
        self.comp = comp
        self.redis = comp.redis

    @property
    def _list_key(self):
        return self.list_key_templ.format_map({self.comp.MY_TYPE: self.comp.my_id})

    def get_objects(self, cls, load=True):
        all_objs = self.get_keys()

        obj_list = []

        for val in all_objs:
            obj = cls(my_id=val,
                      redis=self.redis,
                      access=self.comp.access)

            obj.owner = self.comp
            if load:
                obj.load()

            obj_list.append(obj)

        return obj_list

    def add_object(self, obj, owner=True):
        self.redis.sadd(self._list_key, obj.my_id)

        if owner:
            obj.owner = self.comp
            obj['owner'] = self.comp.my_id

    def contains_id(self, obj_id):
        if not obj_id or obj_id == '*':
            return None

        return self.redis.sismember(self._list_key, obj_id)

    def num_objects(self):
        return self.redis.scard(self._list_key)

    def remove_object(self, obj):
        return self.redis.srem(self._list_key, obj.my_id)

    def get_keys(self):
        return self.redis.smembers(self._list_key)


# ============================================================================
class BaseAccess(object):
    def can_read_coll(self, collection):
        return True

    def can_write_coll(self, collection):
        return True

    def can_admin_coll(self, collection):
        return True

    def assert_can_read_coll(self, collection):
        return True

    def assert_can_write_coll(self, collection):
        return True

    def assert_can_admin_coll(self, collection):
        return True

    def assert_is_curr_user(self, user):
        return True

    def assert_is_superuser(self):
        return True
