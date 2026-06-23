import {keyOfElement} from './buttons';
import {state} from './update';
import {warningToastWithBar, errorToast} from '../base';
import {setStoredVote} from './vote-state';

/** Adds handlers to buttons that are visible when voting is enabled. */
export function onReady() {
  // Use a token bucket implementation to allow 10 Votes per minute.
  const maxTokens = 10;
  let currentTokens = maxTokens;
  const bucketLifetime = 30000; // half a minute
  let currentBucket = $.now();

  let lastActivationAt = 0;
  let lastActivationSignature = '';

  function canVote() {
    const now = $.now();
    const timePassed = now - currentBucket;

    if (timePassed > bucketLifetime) {
      currentBucket = now;
      currentTokens = maxTokens - 1;
      return true;
    }

    if (currentTokens > 0) {
      currentTokens--;
      return true;
    }

    const ratio = (bucketLifetime - timePassed) / bucketLifetime;
    warningToastWithBar('You\'re doing that too often');
    $('#vote-timeout-bar').css('transition', 'none');
    $('#vote-timeout-bar').css('width', ratio * 100 + '%');
    $('#vote-timeout-bar')[0].offsetHeight;
    $('#vote-timeout-bar').css({
      'transition': 'width ' + ratio * bucketLifetime / 1000 + 's linear',
      'width': '0%',
    });
    return false;
  }

  function triggerVoteAnimation(buttonElement) {
    if (
      !buttonElement ||
      !(buttonElement instanceof HTMLElement) ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ) {
      return;
    }

    buttonElement.classList.remove('furatic-vote-bump');
    void buttonElement.offsetWidth;
    buttonElement.classList.add('furatic-vote-bump');

    window.setTimeout(function() {
      buttonElement.classList.remove('furatic-vote-bump');
    }, 560);
  }

  function findVoteButton(event) {
    const target = event && event.target instanceof Element ? event.target : null;
    if (!target) {
      return null;
    }

    return target.closest('.vote-up, .vote-down');
  }

  function activationSignature(buttonElement) {
    const button = $(buttonElement).closest('.vote-up, .vote-down');
    const keyedElement = button.closest('[data-queue-key]');
    const key = keyedElement.attr('data-queue-key') || '';
    const id = button.attr('id') || '';
    const direction = button.hasClass('vote-up') ? 'up' : 'down';

    return direction + ':' + key + ':' + id;
  }

  function markActivationHandled(event, buttonElement) {
    const originalEvent = event;

    if (
      event.type === 'pointerup' &&
      event instanceof PointerEvent &&
      event.pointerType === 'mouse' &&
      event.button !== 0
    ) {
      return false;
    }

    const now = Date.now();
    const signature = activationSignature(buttonElement);

    if (signature === lastActivationSignature && now - lastActivationAt < 650) {
      if (originalEvent.cancelable) {
        originalEvent.preventDefault();
      }
      originalEvent.stopImmediatePropagation();
      return false;
    }

    lastActivationSignature = signature;
    lastActivationAt = now;

    if (originalEvent.cancelable) {
      originalEvent.preventDefault();
    }
    originalEvent.stopImmediatePropagation();

    return true;
  }

  function resolveVoteKey(button) {
    if (button.closest('#current-song-card').length > 0) {
      if (state == null || state.currentSong == null) {
        return -1;
      }

      return state.currentSong.queueKey;
    }

    return keyOfElement(button);
  }

  function submitVote(button, key, amount, onFail = null) {
    let votes = button.closest('.queue-entry').find('.queue-vote-count');
    if (votes.length == 0) {
      votes = button.siblings('#current-song-votes');
    }

    const currentVotes = Number(votes.text()) || 0;
    votes.text(String(currentVotes + amount));

    const form = new URLSearchParams();
    form.set('key', String(key));
    form.set('amount', String(amount));
    form.set('csrfmiddlewaretoken', CSRF_TOKEN);

    fetch(urls['musiq']['vote'], {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'X-CSRFToken': CSRF_TOKEN,
      },
      body: form.toString(),
    }).then(async function(response) {
      if (response.ok) {
        return;
      }

      const text = await response.text();
      throw new Error(text || 'Could not register vote');
    }).catch(function(error) {
      errorToast(error && error.message ? error.message : 'Could not register vote');

      const failedVotes = Number(votes.text()) || 0;
      votes.text(String(failedVotes - amount));

      if (onFail) {
        onFail();
      }
    });
  }

  function handleVotePress(buttonElement) {
    const button = $(buttonElement);

    if (button.attr('data-furatic-own-vote-blocked') === 'true') {
      return false;
    }

    const key = resolveVoteKey(button);
    if (key == -1) {
      return false;
    }

    if (!canVote()) {
      return false;
    }

    const direction = button.hasClass('vote-up') ? 'up' : 'down';
    const up = direction === 'up' ? button : button.siblings('.vote-up');
    const down = direction === 'down' ? button : button.siblings('.vote-down');
    const previousState = up.hasClass('pressed') ? '+' : down.hasClass('pressed') ? '-' : '0';

    function applyVisualVoteState(value) {
      up.removeClass('pressed');
      down.removeClass('pressed');

      if (value === '+') {
        up.addClass('pressed');
      } else if (value === '-') {
        down.addClass('pressed');
      }

      setStoredVote(key, value);
    }

    function restorePreviousState() {
      applyVisualVoteState(previousState);
    }

    triggerVoteAnimation(buttonElement);

    if (direction === 'up') {
      if (up.hasClass('pressed')) {
        applyVisualVoteState('0');
        submitVote(button, key, -1, restorePreviousState);
      } else {
        applyVisualVoteState('+');
        submitVote(button, key, down.hasClass('pressed') ? 2 : 1, restorePreviousState);
      }
      return true;
    }

    if (down.hasClass('pressed')) {
      applyVisualVoteState('0');
      submitVote(button, key, 1, restorePreviousState);
    } else {
      applyVisualVoteState('-');
      submitVote(button, key, up.hasClass('pressed') ? -2 : -1, restorePreviousState);
    }

    return true;
  }

  function handleVoteActivation(event) {
    const buttonElement = findVoteButton(event);
    if (!buttonElement) {
      return;
    }

    if (buttonElement.getAttribute('data-furatic-own-vote-blocked') === 'true') {
      return;
    }

    if (!markActivationHandled(event, buttonElement)) {
      return;
    }

    handleVotePress(buttonElement);
  }

  document.addEventListener('pointerup', handleVoteActivation, {
    capture: true,
    passive: false,
  });

  document.addEventListener('touchend', handleVoteActivation, {
    capture: true,
    passive: false,
  });

  document.addEventListener('click', handleVoteActivation, {
    capture: true,
    passive: false,
  });
}

$(document).ready(() => {
  if (!["/musiq/", "/p/"].includes(window.location.pathname)) {
    return;
  }

  onReady();
});
