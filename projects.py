#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Project history and persistence manager for FKVideoPlayer.
Stores recently opened videos, window captures, and custom canvas projects.
"""

import datetime
import json
import os

PROJECTS_CONFIG_PATH = os.path.join(os.path.expanduser('~'), '.fk_videoplayer_projects.json')

class ProjectManager:
    _instance = None

    def __init__(self):
        self.projects = []
        self._load()

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load(self):
        try:
            if os.path.exists(PROJECTS_CONFIG_PATH):
                with open(PROJECTS_CONFIG_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.projects = data.get('projects', [])
        except Exception:
            self.projects = []

    def _save(self):
        try:
            with open(PROJECTS_CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump({'projects': self.projects[:25]}, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def add_project(self, proj_type: str, name: str, path_or_target: str, width: int = 1920, height: int = 1080):
        """proj_type: 'video', 'window', 'blank'"""
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        # Remove existing if same path
        self.projects = [p for p in self.projects if p.get('target') != path_or_target]
        self.projects.insert(0, {
            'type': proj_type,
            'name': name,
            'target': path_or_target,
            'width': width,
            'height': height,
            'date': now
        })
        self._save()

    def get_recent_projects(self):
        return self.projects

    def clear(self):
        self.projects = []
        self._save()
