import os
import docker
from tornado import gen
from dockerspawner import DockerSpawner
import psutil
import pprint
import json

class DemoFormSpawner(DockerSpawner):
    custom_user_options = {}

    def _options_form_default(self):
        label_style = "width: 25%"
        input_style = "width: 75%"
        div_style = "margin-bottom: 16px"
        optional_label = "<span style=\"font-size: 12px; font-weight: 400;\">(optional)</span>"
        additional_info_style="margin-top: 4px; color: rgb(165,165,165); font-size: 12px;"
        description_gpus = 'Leave empty for no GPU, "all" for all GPUs, or a comma-separated list of indices of the GPUs (e.g 0,2).'
        description_env = 'One name=value pair per line, without quotes'
        description_days_to_live = 'Number of days the container should live'

        additional_cpu_info=psutil.cpu_count()
        additional_memory_info=round(psutil.virtual_memory().total/1024/1024/1024, 1)
        additional_gpu_info=self.get_gpu_info()

        # update image selection with versions
        reg = "mltools.docker.repositories.sap.ondemand.com"
        client = docker.from_env()
        images = client.images.list()
        stack = []

        for img in images:
            if len(img.tags) and reg in img.tags[0]:
                label = img.tags[0].split("/")[1]
                stack.append(label)

        option_tag = ""
        for img in stack:
            if "ml-jupyterhub-" in img:
                name = img.replace("ml-jupyterhub-", "")
            else:
                name = img
            option_tag = option_tag + '<option value="'+reg+'/'+img+'">'+name+'</option>'

        with open("/etc/jupyterhub/spawner_form.html") as file:
            form = file.read()
            form = form.format(
                div_style=div_style,
                label_style=label_style,
                input_style=input_style,
                optional_label=optional_label,
                additional_info_style=additional_info_style,
                additional_cpu_info=additional_cpu_info,
                additional_memory_info=additional_memory_info,
                additional_gpu_info=additional_gpu_info,
                description_gpus=description_gpus,
                description_env=description_env,
                option_tag=option_tag
            )
        return form

    def options_from_form(self, formdata):
        self.custom_user_options["cpu_limit"] = formdata.get('cpu_limit')[0]
        self.custom_user_options["mem_limit"] = formdata.get('mem_limit')[0]

        env = {}
        env_lines = formdata.get('env', [''])

        for line in env_lines[0].splitlines():
            if line:
                key, value = line.split('=', 1)
                env[key.strip()] = value.strip()

        self.custom_user_options['env'] = env
        self.custom_user_options['gpus'] = formdata.get('gpus')[0]
        self.custom_user_options['stack'] = formdata.get('stack')[0]
        self.custom_user_options["shm_size"] = formdata.get('shm_size')[0]

        # self.image = self.custom_user_options["stack"]
        # self.cpu_limit = int(self.custom_user_options["cpu_limit"])

        ## Doing some dirty work here
        # self.customize()
        # self.cpu_limit = int(self.custom_user_options["cpu_limit"])
        # self.new_creating = True

        return self.custom_user_options


    @gen.coroutine
    def start(self) -> (str, int):
        """Set custom configuration during start before calling the super.start method of Dockerspawner
        
        Returns:
            (str, int): container's ip address or '127.0.0.1', container's port
        """

        self.saved_user_options = self.custom_user_options

        self.image = self.custom_user_options["stack"]

        extra_host_config = {}
        if self.custom_user_options["cpu_limit"]:
            # nano_cpus cannot be bigger than the number of CPUs of the machine (this method would currently not work in a cluster, as machines could be different than the machine where the runtime-manager and this code run.
            max_available_cpus = psutil.cpu_count()
            limited_cpus = min( float(self.custom_user_options["cpu_limit"]), max_available_cpus )

            # the nano_cpu parameter of the Docker client expects an integer, not a float
            nano_cpus = int(limited_cpus * 1e9)
            extra_host_config['nano_cpus'] = nano_cpus
            # DockerSpawner.extra_host_config.set_metadata("nano_cpus", nano_cpus)
        else:
            nano_cpus = int(4 * 1e9)
            extra_host_config["nano_cpus"] = nano_cpus

        if self.custom_user_options["mem_limit"]:
            extra_host_config["mem_limit"] = str(self.custom_user_options["mem_limit"]) + "gb"
            # DockerSpawner.extra_host_config.set_metadata("mem_limit", self.custom_user_options["mem_limit"]+"gb")
        else:
            extra_host_config["mem_limit"] = "10gb"

        extra_create_kwargs = {}
        # set default label 'origin' to know for sure which containers where started via the hub
        # extra_create_kwargs["labels"] = self.default_labels

        if self.custom_user_options["gpus"]:
            extra_host_config["runtime"] = "nvidia"
            # DockerSpawner.extra_host_config.set_metadata("runtime", "nvidia")
            extra_create_kwargs["labels"] = {}
            extra_create_kwargs["labels"][""] = self.custom_user_options["gpus"]
            # DockerSpawner.extra_create_kwargs.set_metadata("labels", {"nvidia_visible_devices": self.custom_user_options["gpus"]})
        
        if self.custom_user_options["shm_size"]:
            extra_host_config["shm_size"]=self.custom_user_options["shm_size"]

        self.extra_host_config.update(extra_host_config)
        self.extra_create_kwargs.update(extra_create_kwargs)

        print("Extra create kwargs: "+str(self.extra_create_kwargs))
        print("Extra host config: "+str(self.extra_host_config))
        print("Custom Spawner: ")
        for i in dir(self):
            print(i)

        # Mounting volumes for a user
        with open("/etc/jupyterhub/team_map.json") as json_file:
            data = json.load(json_file)
            name = self.user.name
            if name in data:
                for group in data[name]:
                    host_file_name = "/raid/{}".format(group)
                    container_file_name = "/home/jovyan/shared/{}".format(group)

                    if not os.path.exists(host_file_name):
                        os.mkdir(host_file_name)
                        os.system("chown 1000:100 "+host_file_name)

                    self.volumes[host_file_name] = {
                        "bind": container_file_name,
                        "mode": "rw"
                    }

        print("Name: "+str(self.user.name))
        print("Volumes: "+str(self.volumes))

        # Delete existing container when it is created via the options_form UI (to make sure that not an existing container is re-used when you actually want to create a new one)
        # reset the flag afterwards to prevent the container from being removed when just stopped
        # Also make it deletable via the user_options (can be set via the POST API)
        
        res = yield super().start()

        print("Response: "+str(res))

        self.remove = True
        self.new_creating = True
        return res

    def get_gpu_info(self) -> list:
        count_gpu = 0
        try:
            # NOTE: this approach currently only works for nvidia gpus.
            ps = subprocess.Popen(('find', '/proc/irq/', '-name', 'nvidia'), stdout=subprocess.PIPE)
            output = subprocess.check_output(('wc', '-l'), stdin=ps.stdout)
            ps.wait()
            count_gpu = int(output.decode("utf-8"))
        except:
            pass

        return count_gpu

    def get_env(self):
        env = super().get_env()
        if self.custom_user_options["gpus"]:
            env["NVIDIA_VISIBLE_DEVICES"] = self.custom_user_options["gpus"]
        else:
            env["NVIDIA_VISIBLE_DEVICES"] = "all"
        env.update(self.custom_user_options["env"])
        return env

    
def create_dir_hook(spawner):
    username = spawner.user.name
    volume_path=os.path.join('/data/export/', username)
    if not os.path.exists(volume_path):
        os.mkdir(volume_path)
        os.chown(volume_path, 1000, 100)
    volume_path=os.path.join('/raid', username)
    if not os.path.exists(volume_path):
        os.mkdir(volume_path)
        os.chown(volume_path, 1000, 100)
